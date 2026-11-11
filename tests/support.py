"""Shared helpers for the test suite.

* load_app()      loads logic-layer/app.py (the WSGI entry point) against the test database.
* FakeAI          replaces the OpenRouter call, so tests never use a live AI.
* FakeTelegram    replaces the Telegram HTTP calls and records every message.
* ReportedCase    a TestCase whose verify() prints the input, expected and actual
                  values and "test case N pass", so a human can check each result.

Everything is patched at the library boundary (openai / requests / firecrawl), so the same
tests describe the behaviour no matter how the code behind the API is organised.
"""

import contextlib
import importlib.util
import io
import itertools
import logging
import os
import re
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text as sql_text

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ('ai-layer', 'data-layer', 'logic-layer', 'io-layer')

# Every test empties the tables, so only ever run against a disposable database.
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL')
if not TEST_DATABASE_URL:
    raise unittest.SkipTest('Set TEST_DATABASE_URL to a disposable PostgreSQL database to run these tests '
                            '(see README: "Running the tests")')

BASE_ENV = {
    'OPENROUTER_API_KEY': 'test-key',
    'TELEGRAM_BOT_TOKEN': 'test-token',
    'FIRECRAWL_API_KEY': '',
    'SECRET_KEY': 'test-secret',
    'ADMIN_USERNAME': '',
    'ADMIN_PASSWORD': '',
    'DATABASE_URL': TEST_DATABASE_URL,
}
os.environ.update(BASE_ENV)
logging.disable(logging.WARNING)  # keep expected warnings (failed AI calls, ...) out of the report
for _layer in LAYERS:
    _path = str(ROOT / _layer)
    if _path not in sys.path:
        sys.path.insert(0, _path)

TELEGRAM_ERROR_GROUP = '-5212170665'
DON_CHAT = '667740965'
MAN_CHAT = '1116849976'

_engine = create_engine(TEST_DATABASE_URL)
_migrated = False


def migrate_database() -> None:
    global _migrated
    if not _migrated:
        os.environ['DATABASE_URL'] = TEST_DATABASE_URL
        alembic_command.upgrade(AlembicConfig(str(ROOT / 'data-layer' / 'alembic.ini')), 'head')
        _migrated = True


def reset_database() -> None:
    """Empty every table and restart the id numbering so each test starts clean."""
    migrate_database()
    with _engine.begin() as connection:
        connection.execute(sql_text(
            'TRUNCATE clause_references, clauses, analyses, legal_references, users RESTART IDENTITY'))
        connection.execute(sql_text('ALTER SEQUENCE users_id_seq RESTART'))
        connection.execute(sql_text('ALTER SEQUENCE analyses_id_seq RESTART'))


def run_sql(statement: str, **params):
    with _engine.begin() as connection:
        return connection.execute(sql_text(statement), params)


def load_app(env=None):
    """Import logic-layer/app.py afresh with the test environment and return the module."""
    migrate_database()
    with patch.dict(os.environ, env or {}):
        spec = importlib.util.spec_from_file_location('logic_app', ROOT / 'logic-layer' / 'app.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


_shared_app = None


def get_app():
    """One shared app for most tests (each load opens its own connection pool)."""
    global _shared_app
    if _shared_app is None:
        _shared_app = load_app()
    return _shared_app


# --- fake AI --------------------------------------------------------------------------

DETECTION_MARKER = 'expert contract lawyer'
WORKAROUND_MARKER = 'contract negotiation expert'


def ai_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeAI:
    """Stands in for client.chat.completions.create.

    detection / workaround: JSON text (or an Exception) returned for each pipeline stage.
    failing_models: model names that raise instead of answering.
    """

    def __init__(self, detection='[]', workaround=None, failing_models=(), always_fail=None,
                 fail_first_calls=0, failure=None):
        self.detection = detection
        self.workaround = workaround
        self.failing_models = set(failing_models)
        self.always_fail = always_fail
        self.fail_first_calls = fail_first_calls
        self.failure = failure
        self.calls = []

    def __call__(self, *args, **kwargs):
        model = kwargs.get('model')
        messages = kwargs.get('messages') or []
        self.calls.append({'model': model, 'messages': messages})
        if self.always_fail is not None or model in self.failing_models or len(self.calls) <= self.fail_first_calls:
            raise self.always_fail or self.failure or RuntimeError('503 provider down')
        system = messages[0]['content'] if messages else ''
        if DETECTION_MARKER in system:
            outcome = self.detection
        elif WORKAROUND_MARKER in system:
            if self.workaround is not None:
                outcome = self.workaround
            else:
                outcome = re.search(r'\[.*\]', messages[1]['content'], re.DOTALL).group()
        else:
            outcome = 'OK'
        if isinstance(outcome, Exception):
            raise outcome
        return ai_response(outcome)

    @property
    def models_tried(self):
        return [call['model'] for call in self.calls]

    @contextmanager
    def installed(self):
        with patch('openai.resources.chat.completions.Completions.create', new=self):
            yield self


# --- fake Telegram ---------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class FakeTelegram:
    """Records every message and plays back queued Fix / Stop button presses."""

    MAX_EMPTY_POLLS = 3

    def __init__(self, button_presses=()):
        self.button_presses = list(button_presses)
        self.messages = []      # (kind, chat_id, text)
        self.edited_markups = 0
        self._update_id = 1000
        self._empty_polls = 0

    def _record(self, url, params, body):
        data = body or params or {}
        self.messages.append(('send', str(data.get('chat_id')), data.get('text', ''), data.get('reply_markup')))

    def get(self, url, params=None, json=None, timeout=None, **kwargs):
        if url.endswith('/getUpdates'):
            return self._updates(params or {})
        if url.endswith('/sendMessage'):
            self._record(url, params, json)
        return FakeResponse({'ok': True})

    def post(self, url, json=None, params=None, timeout=None, **kwargs):
        if url.endswith('/sendMessage'):
            self._record(url, params, json)
        elif url.endswith('/editMessageReplyMarkup'):
            self.edited_markups += 1
        return FakeResponse({'ok': True})

    def _updates(self, params):
        if params.get('offset') == -1 or not self.button_presses:
            if params.get('offset') != -1:
                self._empty_polls += 1
                if self._empty_polls > self.MAX_EMPTY_POLLS:
                    raise AssertionError('The app is waiting for a Telegram button press nobody queued')
            return FakeResponse({'ok': True, 'result': []})
        self._update_id += 1
        return FakeResponse({'ok': True, 'result': [{
            'update_id': self._update_id,
            'callback_query': {
                'data': self.button_presses.pop(0),
                'message': {'chat': {'id': int(TELEGRAM_ERROR_GROUP)}, 'message_id': 7},
            },
        }]})

    def sent_to(self, chat_id):
        return [message for message in self.messages if message[1] == str(chat_id)]

    @contextmanager
    def installed(self):
        with patch.object(requests, 'get', side_effect=self.get), \
                patch.object(requests, 'post', side_effect=self.post):
            yield self


# --- API helpers -----------------------------------------------------------------------

def make_clause(**overrides):
    clause = {
        'clause_type': 'Unlimited Liability',
        'risk_level': 'HIGH',
        'issue_description': 'Liability has no cap',
        'workaround': 'Negotiate a cap',
        'clause_text': 'The supplier is liable for all damages.',
        'line_number': 'Page 2, Clause 3(f)',
    }
    clause.update(overrides)
    return clause


def upload(client, text='A contract for testing', filename='contract.txt', context=None, raw=None):
    data = {'contract': (io.BytesIO(raw if raw is not None else text.encode('utf-8')), filename)}
    if context is not None:
        data['context'] = context
    return client.post('/api/analyze', data=data, content_type='multipart/form-data')


def register(client, username='alice', password='password123'):
    return client.post('/api/auth/register', json={'username': username, 'password': password})


def login(client, username='alice', password='password123'):
    return client.post('/api/auth/login', json={'username': username, 'password': password})


def make_admin(username='admin', password='adminpass1'):
    """Insert an admin account directly (registration only ever creates customers)."""
    from werkzeug.security import generate_password_hash
    run_sql("INSERT INTO users (id, username, password_hash, role) VALUES ('us_900', :u, :h, 'admin')",
            u=username, h=generate_password_hash(password))
    return username, password


# --- human readable reporting --------------------------------------------------------

_case_numbers = itertools.count(1)
RESULTS = []  # (number, title, passed) of every verify() call, for the summary
_MAX_WIDTH = 400


def _short(value):
    text = value if isinstance(value, str) else repr(value)
    text = text.replace('\n', '\\n')
    return text if len(text) <= _MAX_WIDTH else text[:_MAX_WIDTH] + ' ...'


class ReportedCase(unittest.TestCase):
    """verify() prints one numbered, human-checkable test case and asserts it.
    Anything the application itself prints is hidden so the report stays readable."""

    def run(self, result=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return super().run(result)

    def verify(self, title, given, expected, actual, check=None):
        number = next(_case_numbers)
        passed = check(actual, expected) if check else actual == expected
        RESULTS.append((number, title, passed))
        print(f'\nTest case {number}: {title}\n'
              f'  Input    : {_short(given)}\n'
              f'  Expected : {_short(expected)}\n'
              f'  Actual   : {_short(actual)}\n'
              f'  Result   : test case {number} {"pass" if passed else "FAIL"}', file=sys.__stdout__, flush=True)
        self.assertTrue(passed, f'Test case {number} failed: {title}')
        return actual
