"""AI failover and Telegram notifications, tested directly on AIManager and TelegramNotifier.

(Rewritten for the current design: the engineering alert is sent when every model has failed,
and an engineer answers Fix / Stop in Telegram.)
"""

import json
import unittest
from unittest.mock import patch

import requests

from support import TELEGRAM_ERROR_GROUP, FakeAI, FakeTelegram, ReportedCase, make_clause

from ai_manager import AIManager, RecoverySession
from notifications import ApprovalChannel, Notifier, TelegramNotifier

PRIMARY = AIManager.PRIMARY_MODEL
SECOND_MODEL = 'google/gemma-pro-1.5-2b-it:free'


class FailoverCase(ReportedCase):
    def setUp(self):
        self.telegram = FakeTelegram()
        self.notifier = TelegramNotifier('test-token')
        self.manager = AIManager('test-key', approval=self.notifier)

    def run_with(self, ai, action):
        with ai.installed(), self.telegram.installed():
            return action()

    def detect(self, ai, recovery=None):
        recovery = recovery or RecoverySession()
        return self.run_with(ai, lambda: self.manager.detect_clauses('A contract', '', recovery))

    def group_texts(self):
        return [message[2] for message in self.telegram.sent_to(TELEGRAM_ERROR_GROUP)]


class FailoverTests(FailoverCase):
    def test_unavailable_model_is_skipped_without_retry(self):
        ai = FakeAI(detection='[]', failing_models={PRIMARY}, failure=RuntimeError('404 model not found'))
        self.detect(ai)
        self.verify('A 404 model is skipped immediately and the next model answers',
                    f"Primary model raises '404 model not found'",
                    [PRIMARY, SECOND_MODEL], ai.models_tried)

    def test_transient_failure_is_retried_on_same_model(self):
        ai = FakeAI(detection='[]', fail_first_calls=1, failure=RuntimeError('429 rate limit'))
        self.detect(ai)
        self.verify('A transient failure is retried once on the same model',
                    "First call raises '429 rate limit'", [PRIMARY, PRIMARY], ai.models_tried)
        self.verify('No alert is sent when a retry succeeds', 'Messages sent to Telegram', [],
                    self.telegram.messages)

    def test_error_categories_in_the_alert(self):
        cases = [
            ('context_length_exceeded', 'Context length exceeded - document too large for AI processing'),
            ('429 Too Many Requests', 'Rate limit exceeded - too many requests'),
            ('request timed out', 'AI request timed out'),
            ('401 Unauthorized', 'AI authentication failed'),
            ('invalid request body', 'Invalid request to AI service'),
        ]
        for error_text, category in cases:
            self.telegram = FakeTelegram(button_presses=['reject'])
            ai = FakeAI(always_fail=RuntimeError(error_text))
            with self.assertRaises(RuntimeError):
                self.detect(ai)
            self.verify(f"Alert category for '{error_text}'", f"Every model raises '{error_text}'",
                        f'⚠️ AI Processing Error\n\n{category}', self.group_texts()[0])

    def test_alert_goes_out_once_per_analysis(self):
        self.telegram = FakeTelegram(button_presses=['reject'])
        recovery = RecoverySession()
        ai = FakeAI(always_fail=RuntimeError('503 provider down'))
        with self.assertRaises(RuntimeError):
            self.detect(ai, recovery)
        with self.assertRaises(RuntimeError) as second:
            self.detect(ai, recovery)
        alerts = [text for text in self.group_texts() if text.startswith('⚠️')]
        self.verify('A second AI call in the same analysis does not alert again',
                    'Two failing calls sharing one RecoverySession',
                    (1, True), (len(alerts), str(second.exception).startswith('All AI models are currently unavailable')))

    def test_unknown_button_falls_through_to_unavailable(self):
        self.telegram = FakeTelegram(button_presses=['something-else'])
        with self.assertRaises(RuntimeError) as error:
            self.detect(FakeAI(always_fail=RuntimeError('503 provider down')))
        self.verify('An unrecognised button press ends in the generic unavailable error',
                    "Telegram button 'something-else'",
                    True, str(error.exception).startswith('All AI models are currently unavailable'))

    def test_missing_api_key(self):
        manager = AIManager(None, approval=self.notifier)
        with self.assertRaises(ValueError) as error:
            manager.detect_clauses('A contract', '', RecoverySession())
        self.verify('No OpenRouter key is reported before any call is made', 'AIManager(api_key=None).detect_clauses',
                    'OPENROUTER_API_KEY not configured', str(error.exception))


class ResponseValidationTests(FailoverCase):
    def test_only_well_formed_clauses_are_kept(self):
        raw = [make_clause(), 'a stray string', {'unrelated': 1}, make_clause(clause_type='Termination')]
        clauses = self.detect(FakeAI(detection='Here you go: ' + json.dumps(raw)))
        self.verify('Valid clauses are kept, strings and objects without a type or text are dropped',
                    "AI answer: [clause, 'a stray string', {'unrelated': 1}, clause]",
                    ['Unlimited Liability', 'Termination'], [c['clause_type'] for c in clauses])

    def test_malformed_and_missing_json(self):
        for title, answer in (('malformed JSON', '[{invalid}]'), ('no JSON at all', 'I cannot help'),
                              ('a JSON object, not an array', '{"clause_type": "x"}')):
            self.verify(f'AI answer with {title} gives no clauses', f'AI answer: {answer}',
                        [], self.detect(FakeAI(detection=answer)))

    def test_workaround_failure_keeps_original_clauses(self):
        clauses = [make_clause()]
        ai = FakeAI(workaround='not JSON')
        result = self.run_with(ai, lambda: self.manager.improve_workarounds(clauses, RecoverySession()))
        self.verify('If the improvement step returns garbage the original clauses are kept',
                    "Workaround stage answers 'not JSON'", clauses, result)


class TelegramNotifierTests(ReportedCase):
    def test_interfaces(self):
        notifier = TelegramNotifier('token')
        self.verify('TelegramNotifier realises both interfaces',
                    'isinstance(TelegramNotifier, Notifier / ApprovalChannel)',
                    (True, True), (isinstance(notifier, Notifier), isinstance(notifier, ApprovalChannel)))
        errors = []
        for interface in (Notifier, ApprovalChannel):
            try:
                interface()
            except TypeError as error:
                errors.append(type(error).__name__)
        self.verify('The interfaces cannot be instantiated', 'Notifier() and ApprovalChannel()',
                    ['TypeError', 'TypeError'], errors)

    def test_missing_token(self):
        telegram = FakeTelegram()
        notifier = TelegramNotifier(None)
        with telegram.installed():
            notifier.send('hello', '123')
            notifier.announce('hello')
            with self.assertRaises(RuntimeError) as error:
                notifier.request_decision('prompt')
        self.verify('Without a token nothing is sent and a decision cannot be requested',
                    'TelegramNotifier(token=None): send, announce, request_decision',
                    (0, 'Telegram bot token not configured'), (len(telegram.messages), str(error.exception)))

    def test_delivery_failure_does_not_raise(self):
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('network down')):
            result = TelegramNotifier('token').send('hello', '123')
        self.verify('A failed delivery is logged, not raised', "send() while the network is down", None, result)

    def test_send_targets_the_recipient(self):
        telegram = FakeTelegram()
        with telegram.installed():
            TelegramNotifier('token').send('hello', '4242')
        self.verify('send() delivers the text to the given chat', "send('hello', '4242')",
                    [('4242', 'hello')], [(message[1], message[2]) for message in telegram.messages])


if __name__ == '__main__':
    unittest.main()
