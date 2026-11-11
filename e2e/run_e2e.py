"""End-to-end test of the running Docker Compose stack (nginx + app + PostgreSQL + fake AI + fake Telegram).

It talks to the app over HTTP exactly like the browser does and prints every test case with its
input, expected result and actual result. Exit code 0 means every case passed.
"""

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = os.environ.get('BASE_URL', 'http://app')
FAKE_AI_URL = os.environ.get('FAKE_AI_URL', 'http://fake-ai:8000')
FAKE_TELEGRAM_URL = os.environ.get('FAKE_TELEGRAM_URL', 'http://fake-telegram:8001')

# The chats the app alerts by default (see logic-layer/app.py and notifications.py)
DON_ID = '667740965'
MAN_ID = '1116849976'
GROUP_ID = '-5212170665'

results = []


class Browser:
    """One signed-in (or signed-out) visitor; keeps its own cookies."""

    def __init__(self):
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method, path, body=None, upload=None):
        """Returns (status, parsed JSON or text). upload is (filename, text, context)."""
        headers, data = {'Host': 'localhost'}, None  # nginx only serves the app for the host name "localhost"
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        elif upload is not None:
            filename, text, context = upload
            boundary = uuid.uuid4().hex
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="contract"; filename="{filename}"\r\n'
                     f'Content-Type: text/plain\r\n\r\n{text}\r\n',
                     f'--{boundary}\r\nContent-Disposition: form-data; name="context"\r\n\r\n{context}\r\n',
                     f'--{boundary}--\r\n']
            data = ''.join(parts).encode()
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=60) as response:
                status, raw = response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read().decode()
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw


def verify(title, given, expected, actual):
    number = len(results) + 1
    passed = actual == expected
    results.append((number, title, passed))
    print(f'\nTest case {number}: {title}\n  Input    : {given}\n  Expected : {expected}\n'
          f'  Actual   : {actual}\n  Result   : test case {number} {"pass" if passed else "FAIL"}', flush=True)


def fake_ai_calls():
    with urllib.request.urlopen(FAKE_AI_URL + '/calls', timeout=10) as response:
        return json.load(response)['calls']


def post_json(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def fail_next_ai_requests(count):
    post_json(FAKE_AI_URL + '/fail', {'count': count})


def press_telegram_button(data):
    """Play the engineer pressing Fix ('accept') or Stop ('reject') on the alert."""
    post_json(FAKE_TELEGRAM_URL + '/press', {'data': data})


def telegram_messages():
    with urllib.request.urlopen(FAKE_TELEGRAM_URL + '/messages', timeout=10) as response:
        return json.load(response)


def texts_sent_to(chat_id):
    return [m['text'] for m in telegram_messages() if m['chat_id'] == chat_id]


def alert_text(flagged):
    """The message the app sends for an analysis of `flagged` HIGH clauses by alice (us_001)."""
    return ('Contract clause analysis completed\nUser ID: us_001\nUsername: alice\n'
            f'Total clause flagged: {flagged}\nHigh risk: {flagged}\nMedium Risk: 0\nLow risk: 0')


def wait_until_ready():
    """nginx starts before gunicorn, so wait for a real answer from the app (not a 502)."""
    for _ in range(60):
        try:
            status, body = Browser().request('GET', '/api/auth/me')
            if status == 401 and isinstance(body, dict):
                return
        except OSError:
            pass
        time.sleep(2)
    sys.exit('The app did not become ready in time')


def clause_types(analysis):
    return [clause['clause_type'] for clause in analysis['clauses']]


def main():
    wait_until_ready()
    alice, bob, visitor = Browser(), Browser(), Browser()
    version_1 = 'VERSION 1. The supplier is liable for all damages. The contract renews every year.'
    version_2 = 'VERSION 2. The supplier is liable for all damages. Either side may end the contract.'

    status, page = visitor.request('GET', '/')
    verify('The web page is served by nginx', 'GET /', (200, True), (status, 'html' in str(page).lower()))

    status, health = visitor.request('GET', '/api/health')
    verify('Health check reaches the (fake) AI through the app', 'GET /api/health',
           ('healthy', True, 4), (health['status'], health['openrouter_configured'], len(health['working_models'])))

    status, body = visitor.request('GET', '/api/analyses')
    verify('History needs a login', 'GET /api/analyses (not signed in)',
           (401, 'Please sign in to continue.'), (status, body['error']))

    status, body = alice.request('POST', '/api/auth/register', {'username': 'alice', 'password': 'password123'})
    verify('Register a customer (saved in PostgreSQL)', "POST /api/auth/register {'username': 'alice', ...}",
           (201, 'us_001', 'customer'), (status, body['user']['id'], body['user']['role']))

    status, body = alice.request('GET', '/api/auth/me')
    verify('The login cookie works through nginx', 'GET /api/auth/me', (200, 'alice'),
           (status, body['user']['username']))

    before = fake_ai_calls()
    status, first = alice.request('POST', '/api/analyze', upload=('contract_v1.txt', version_1, ''))
    verify('Upload a contract: the fake AI flags two clauses',
           'POST /api/analyze contract_v1.txt (text contains VERSION 1)',
           (200, 'an_001', 'done', ['Unlimited Liability', 'Automatic Renewal'], {'high': 1, 'medium': 0, 'low': 1}),
           (status, first['id'], first['status'], clause_types(first), first['counts']))
    verify('Clause details come back in the shape the page renders', 'clauses[0] of the response above',
           ('HIGH', 'Page 2, Clause 3(f)', 2, '3(f)'),
           (first['clauses'][0]['risk_level'], first['clauses'][0]['line_number'],
            first['clauses'][0]['location']['page'], first['clauses'][0]['location']['clause_ref']))
    verify('The pipeline asked the AI twice (detect, then improve workarounds)', 'fake AI /calls delta',
           2, fake_ai_calls() - before)

    before = fake_ai_calls()
    status, again = alice.request('POST', '/api/analyze', upload=('contract_v1.txt', version_1, ''))
    verify('The same contract is answered from the database, not the AI', 'POST /api/analyze (same file again)',
           (200, 'an_001', True, 0), (status, again['id'], again['cached'], fake_ai_calls() - before))

    status, second = alice.request('POST', '/api/analyze', upload=('contract_v2.txt', version_2, 'revised'))
    verify('A revised contract is analysed again', 'POST /api/analyze contract_v2.txt (VERSION 2) with context',
           (200, 'an_002', ['Unlimited Liability', 'Termination']),
           (status, second['id'], clause_types(second)))

    status, clean = alice.request('POST', '/api/analyze', upload=('clean.txt', 'CLEAN CONTRACT with nothing risky', ''))
    verify('A contract with no risky clauses is a normal result', 'POST /api/analyze (text contains CLEAN CONTRACT)',
           (200, 'done', 'No problematic clauses detected'), (status, clean['status'], clean['message']))

    status, body = alice.request('POST', '/api/analyze', upload=('notes.xyz', 'text', ''))
    verify('An unsupported file type gives a generic error', 'POST /api/analyze notes.xyz',
           (500, True), (status, 'Reference:' in body['error']))

    status, history = alice.request('GET', '/api/analyses')
    verify('History lists the analyses newest first', 'GET /api/analyses',
           (200, ['an_003', 'an_002', 'an_001']), (status, [a['id'] for a in history['analyses']]))

    status, compared = alice.request('GET', '/api/compare?a=an_001&b=an_002')
    pair = compared['in_both'][0]
    verify('Compare two analyses', 'GET /api/compare?a=an_001&b=an_002',
           ('Unlimited Liability', 'lower', ['Automatic Renewal'], ['Termination']),
           (pair['a']['clause_type'], pair['risk_change'],
            [c['clause_type'] for c in compared['only_a']], [c['clause_type'] for c in compared['only_b']]))

    status, options = alice.request('GET', '/api/analyses/an_002/compare-options')
    verify('Compare options list the other finished analyses', 'GET /api/analyses/an_002/compare-options',
           (200, ['an_003', 'an_001']),
           (status, [a['id'] for a in options['same_file'] + options['others']]))

    bob.request('POST', '/api/auth/register', {'username': 'bob', 'password': 'password123'})
    status, body = bob.request('GET', '/api/analyses/an_001')
    status_list, listing = bob.request('GET', '/api/analyses')
    verify("Another user cannot see alice's analyses", "As bob: GET /api/analyses/an_001 and GET /api/analyses",
           (404, []), (status, listing['analyses']))

    status, body = alice.request('DELETE', '/api/analyses/an_001')
    status_after, _ = alice.request('GET', '/api/analyses/an_001')
    verify('Delete an analysis', 'DELETE /api/analyses/an_001, then GET it',
           (200, {'deleted': 'an_001'}, 404), (status, body, status_after))

    alice.request('POST', '/api/auth/logout')
    status, body = alice.request('GET', '/api/auth/me')
    verify('Logging out ends the session', 'POST /api/auth/logout, then GET /api/auth/me',
           (401, 'Not signed in'), (status, body['error']))

    status, body = alice.request('POST', '/api/auth/login', {'username': 'alice', 'password': 'wrong-password'})
    verify('A wrong password is refused', "POST /api/auth/login with a wrong password",
           (401, 'Incorrect username or password.'), (status, body['error']))
    alice.request('POST', '/api/auth/login', {'username': 'alice', 'password': 'password123'})
    status, history = alice.request('GET', '/api/analyses')
    verify('After signing in again, the saved analyses are still there', 'POST /api/auth/login, GET /api/analyses',
           ['an_003', 'an_002'], [a['id'] for a in history['analyses']])

    verify('Small analyses (2 flagged clauses) send no Telegram alert', 'Messages the fake Telegram bot received so far',
           [], telegram_messages())

    seven = 'SEVEN RISKS. This contract is full of risky clauses.'
    status, body = alice.request('POST', '/api/analyze', upload=('seven.txt', seven, ''))
    verify('A contract with 7 risks is analysed', 'POST /api/analyze seven.txt (text contains SEVEN RISKS)',
           (200, 'done', 7), (status, body['status'], body['total_flagged']))
    verify('7 flagged clauses alert the DON_ID chat only', 'Messages the fake Telegram bot received',
           {DON_ID: [alert_text(7)], MAN_ID: []},
           {DON_ID: texts_sent_to(DON_ID), MAN_ID: texts_sent_to(MAN_ID)})

    nine = 'NINE RISKS. This contract is even riskier.'
    status, body = alice.request('POST', '/api/analyze', upload=('nine.txt', nine, ''))
    verify('A contract with more than 8 risks (9) is analysed', 'POST /api/analyze nine.txt (text contains NINE RISKS)',
           (200, 'done', 9), (status, body['status'], body['total_flagged']))
    verify('9 flagged clauses alert the MAN_ID chat (and DON_ID is not alerted again)',
           'Messages the fake Telegram bot received',
           {DON_ID: [alert_text(7)], MAN_ID: [alert_text(9)]},
           {DON_ID: texts_sent_to(DON_ID), MAN_ID: texts_sent_to(MAN_ID)})

    outage = 'VERSION 1. Contract analysed while the AI is down.'
    fail_next_ai_requests(16)  # every model, twice each
    press_telegram_button('accept')
    status, body = alice.request('POST', '/api/analyze', upload=('outage.txt', outage, ''))
    verify('AI down and an engineer presses Fix in Telegram: the analysis still completes',
           'Every AI model refuses 16 requests; Telegram button "accept"; POST /api/analyze outage.txt',
           (200, 'done', ['Unlimited Liability', 'Automatic Renewal']),
           (status, body['status'], clause_types(body)))
    group = [m for m in telegram_messages() if m['chat_id'] == GROUP_ID]
    verify('The engineering group got the alert with Fix / Stop buttons, then the recovery messages',
           f'Messages sent to the engineering group {GROUP_ID}',
           (True, ['accept', 'reject'], ['🔄 Attempting manual AI recovery...', '✅ Backup AI successfully recovered!']),
           (group[0]['text'].startswith('⚠️ AI Processing Error'), group[0]['buttons'],
            [m['text'] for m in group[1:]]))

    failed = [r for r in results if not r[2]]
    print('\n' + '=' * 72)
    print(f'SUMMARY: {len(results)} test cases, {len(results) - len(failed)} pass, {len(failed)} FAIL')
    for number, title, _ in failed:
        print(f'  FAIL  test case {number}: {title}')
    print('=' * 72)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
