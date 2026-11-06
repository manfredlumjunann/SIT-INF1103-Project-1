import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import openai


ROOT = Path(__file__).resolve().parents[1]
_env_values = {
    'OPENROUTER_API_KEY': 'test-key',
    'TELEGRAM_BOT_TOKEN': 'test-token',
    'FIRECRAWL_API_KEY': '',
    'SECRET_KEY': 'test-secret',
    'ADMIN_USERNAME': '',
    'ADMIN_PASSWORD': '',
}
_storage = tempfile.TemporaryDirectory()
_env_values['DATA_DIR'] = _storage.name
_old_env = {key: os.environ.get(key) for key in _env_values}
os.environ.update(_env_values)

if not os.getenv('TEST_INSTALLED_APP'):
    sys.path.insert(0, str(ROOT / 'ai-layer'))
    sys.path.insert(0, str(ROOT / 'data-layer'))

import ai_service

if os.getenv('TEST_INSTALLED_APP'):
    import app as logic
else:
    spec = importlib.util.spec_from_file_location('logic_app', ROOT / 'logic-layer' / 'app.py')
    logic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(logic)


def tearDownModule():
    _storage.cleanup()
    for key, value in _old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class AINotificationTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        logic.data_manager.init_storage(self.storage.name)
        self.addCleanup(self.storage.cleanup)
        self.models = patch.multiple(ai_service, PRIMARY_MODEL='primary', FALLBACK_MODELS=['fallback'])
        self.models.start()
        self.addCleanup(self.models.stop)
        self.telegram = patch.object(logic.requests, 'post')
        self.post = self.telegram.start()
        self.post.return_value.json.return_value = {'ok': True}
        self.post.return_value.status_code = 200
        self.addCleanup(self.telegram.stop)
        self.telegram_get = patch.object(logic.requests, 'get')
        self.get = self.telegram_get.start()
        self.get.return_value.json.return_value = {'ok': True}
        self.get.return_value.status_code = 200
        self.addCleanup(self.telegram_get.stop)
        self.client = logic.app.test_client()
        result = self.client.post('/api/auth/register', json={
            'username': 'test-user', 'password': 'test-password',
        })
        self.assertEqual(result.status_code, 201)

    def use_provider(self, outcomes):
        create = Mock(side_effect=outcomes)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        provider = patch.object(ai_service, 'get_openrouter_client', return_value=client)
        provider.start()
        self.addCleanup(provider.stop)
        return create

    def analyze(self):
        return self.client.post('/api/analyze', data={
            'contract': (io.BytesIO(b'A contract for testing'), 'contract.txt'),
        }, content_type='multipart/form-data')

    def alerts(self):
        return [call.kwargs['json']['text'] for call in self.post.call_args_list]

    def test_primary_failure_alerts_and_fallback_succeeds(self):
        create = self.use_provider([RuntimeError('404 missing'), response('[]')])
        result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertEqual([call.kwargs['model'] for call in create.call_args_list],
                         ['primary', 'fallback'])
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('Primary model primary failed: Model unavailable', self.alerts()[0])
        self.assertEqual(self.post.call_args.kwargs['json']['chat_id'], logic.TELEGRAM_GROUP_CHAT_ID)

    def test_invalid_model_id_includes_model_and_http_status(self):
        http_response = httpx.Response(400, request=httpx.Request('POST', 'https://example.invalid'))
        error = openai.BadRequestError(
            'model is not a valid model ID; private-user',
            response=http_response,
            body={'error': {'message': 'model is not a valid model ID', 'userid': 'private-user'}},
        )
        self.use_provider([error, error])
        self.assertEqual(self.analyze().status_code, 503)
        self.assertEqual(len(self.alerts()), 2)
        self.assertIn('primary: Invalid model ID (HTTP 400)', self.alerts()[1])
        self.assertIn('fallback: Invalid model ID (HTTP 400)', self.alerts()[1])
        self.assertNotIn('private-user', str(self.alerts()))

    def test_primary_retry_success_does_not_alert(self):
        create = self.use_provider([RuntimeError('429 rate limit'), response('[]')])
        self.assertEqual(self.analyze().status_code, 200)
        self.assertEqual([call.kwargs['model'] for call in create.call_args_list],
                         ['primary', 'primary'])
        self.assertEqual(self.alerts(), [])

    def test_all_models_fail_and_alerts_do_not_leak_provider_text(self):
        self.use_provider([RuntimeError('404 secret-primary'), RuntimeError('429 secret-fallback'),
                           RuntimeError('429 secret-fallback')])
        result = self.analyze()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(len(self.alerts()), 2)
        self.assertIn('Primary model primary failed', self.alerts()[0])
        self.assertIn('fallback: Rate limit or quota exceeded', self.alerts()[1])
        self.assertNotIn('secret-', str(self.alerts()))
        self.assertNotIn('secret-', result.get_data(as_text=True))

    def test_missing_primary_alerts_and_uses_fallback(self):
        ai_service.PRIMARY_MODEL = ''
        self.use_provider([response('[]')])
        self.assertEqual(self.analyze().status_code, 200)
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn('Primary model is not configured', self.alerts()[0])

    def test_missing_primary_and_fallback_alert_twice(self):
        ai_service.PRIMARY_MODEL = ''
        ai_service.FALLBACK_MODELS = []
        self.assertEqual(self.analyze().status_code, 503)
        self.assertEqual(len(self.alerts()), 2)
        self.assertIn('No primary or fallback AI models are configured', self.alerts()[1])

    def test_removed_model_definitions_are_reported(self):
        del ai_service.PRIMARY_MODEL
        del ai_service.FALLBACK_MODELS
        self.assertEqual(self.analyze().status_code, 503)
        self.assertEqual(len(self.alerts()), 2)
        self.assertIn('Primary model is not configured', self.alerts()[0])
        self.assertIn('No primary or fallback AI models are configured', self.alerts()[1])

    def test_empty_fallback_does_not_alert_if_primary_succeeds(self):
        ai_service.FALLBACK_MODELS = []
        self.use_provider([response('[]')])
        self.assertEqual(self.analyze().status_code, 200)
        self.assertEqual(self.alerts(), [])

    def test_missing_api_key_alerts(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            self.assertEqual(self.analyze().status_code, 503)
        self.assertIn('API key is not configured', self.alerts()[0])

    def test_invalid_detection_response_fails_and_alerts(self):
        self.use_provider([response('[{invalid}]')])
        self.assertEqual(self.analyze().status_code, 503)
        self.assertIn('malformed JSON during clause detection', self.alerts()[0])

    def test_optional_enhancement_failure_alerts_and_keeps_original(self):
        clauses = '[{"clause_type":"Liability","risk_level":"HIGH","clause_text":"text"}]'
        self.use_provider([response(clauses), response('not JSON')])
        with patch.object(logic, 'FIRECRAWL_API_KEY', None):
            result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['clauses'][0]['clause_type'], 'Liability')
        self.assertIn('no JSON array during workaround enhancement', self.alerts()[0])

    def test_exhausted_models_during_enhancement_alert_and_keep_original(self):
        clauses = '[{"clause_type":"Liability","risk_level":"HIGH","clause_text":"text"}]'
        self.use_provider([response(clauses), RuntimeError('404 missing'),
                           RuntimeError('404 missing')])
        result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['clauses'][0]['clause_type'], 'Liability')
        self.assertEqual(len(self.alerts()), 2)
        self.assertIn('Primary model primary failed', self.alerts()[0])
        self.assertIn('All AI models failed', self.alerts()[1])

    def test_optional_research_failure_alerts_and_keeps_analysis(self):
        clauses = '[{"clause_type":"Liability","risk_level":"HIGH","clause_text":"text"}]'
        self.use_provider([response(clauses), response(clauses)])
        with patch.object(logic, 'FIRECRAWL_API_KEY', 'test'), \
             patch.object(logic, 'FirecrawlApp', side_effect=RuntimeError('429 private data')):
            result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertIn('Firecrawl research failed: Rate limit or quota exceeded', self.alerts()[0])
        self.assertNotIn('private data', self.alerts()[0])

    def test_extraction_failure_alerts(self):
        with patch.object(logic, 'extract_text_from_document', side_effect=ValueError('private')):
            result = self.analyze()
        self.assertEqual(result.status_code, 500)
        self.assertIn('Document text extraction failed (ValueError)', self.alerts()[0])
        self.assertNotIn('private', self.alerts()[0])

    def test_nine_flagged_clauses_notify_existing_individual_and_not_again_from_cache(self):
        clauses = [
            {'clause_type': f'Clause {i}', 'risk_level': 'HIGH', 'clause_text': f'Text {i}'}
            for i in range(9)
        ]
        self.use_provider([response(json.dumps(clauses)), response(json.dumps(clauses))])
        first = self.analyze()
        second = self.analyze()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()['cached'])
        self.assertEqual(self.post.call_count, 1)
        payload = self.post.call_args.kwargs['json']
        self.assertEqual(payload['chat_id'], logic.TELEGRAM_HIGH_COUNT_CHAT_ID)
        self.assertIn('Total clause flagged: 9', payload['text'])
        self.assertIn('High risk: 9', payload['text'])

    def test_clause_thresholds_and_recipients(self):
        for total, expected in ((6, None), (7, logic.TELEGRAM_MEDIUM_COUNT_CHAT_ID),
                                (8, logic.TELEGRAM_MEDIUM_COUNT_CHAT_ID),
                                (9, logic.TELEGRAM_HIGH_COUNT_CHAT_ID)):
            with self.subTest(total=total):
                self.post.reset_mock()
                with logic.app.test_request_context():
                    logic.send_telegram_notification(total, 'test-user', [])
                if expected is None:
                    self.post.assert_not_called()
                else:
                    self.assertEqual(self.post.call_args.kwargs['json']['chat_id'], expected)

    def test_clause_telegram_unauthorized_is_logged_as_failure(self):
        self.post.return_value.status_code = 401
        self.post.return_value.json.return_value = {
            'ok': False, 'error_code': 401, 'description': 'Unauthorized',
        }
        self.get.return_value.status_code = 401
        self.get.return_value.json.return_value = self.post.return_value.json.return_value
        with logic.app.test_request_context(), self.assertLogs(logic.logger, level='ERROR') as logs:
            logic.send_telegram_notification(9, 'test-user', [])
        self.assertIn('401', ' '.join(logs.output))
        self.assertIn('check TELEGRAM_BOT_TOKEN', ' '.join(logs.output))
        self.assertNotIn('test-token', ' '.join(logs.output))

    def test_ai_telegram_unauthorized_is_logged_and_fallback_continues(self):
        self.use_provider([RuntimeError('404 missing'), response('[]')])
        self.post.return_value.status_code = 401
        self.post.return_value.json.return_value = {'ok': False, 'error_code': 401}
        with self.assertLogs(logic.logger, level='ERROR') as logs:
            result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertIn('401', ' '.join(logs.output))
        self.assertIn('check TELEGRAM_BOT_TOKEN', ' '.join(logs.output))
        self.assertNotIn('test-token', ' '.join(logs.output))

    def test_non_json_telegram_unauthorized_still_reports_status(self):
        self.post.return_value.status_code = 401
        self.post.return_value.json.side_effect = ValueError('not JSON')
        with logic.app.test_request_context(), self.assertLogs(logic.logger, level='ERROR') as logs:
            logic.send_telegram_notification(9, 'test-user', [])
        self.assertIn('HTTP 401', ' '.join(logs.output))
        self.assertIn('check TELEGRAM_BOT_TOKEN', ' '.join(logs.output))

    def test_telegram_timeout_is_logged_without_leaking_url(self):
        self.post.side_effect = logic.requests.Timeout('https://api.telegram.org/bottest-token/sendMessage')
        with logic.app.test_request_context(), self.assertLogs(logic.logger, level='ERROR') as logs:
            logic.send_telegram_notification(9, 'test-user', [])
        self.assertIn('Timeout', ' '.join(logs.output))
        self.assertNotIn('test-token', ' '.join(logs.output))

    def test_telegram_failure_is_logged_without_blocking_analysis(self):
        self.use_provider([RuntimeError('404 unavailable'), response('[]')])
        self.post.return_value.json.return_value = {'ok': False}
        with self.assertLogs(logic.logger, level='ERROR') as logs:
            result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertIn('Telegram rejected', ' '.join(logs.output))

    def test_invalid_telegram_response_does_not_block_fallback(self):
        self.use_provider([RuntimeError('404 unavailable'), response('[]')])
        self.post.return_value.json.return_value = []
        with self.assertLogs(logic.logger, level='ERROR') as logs:
            result = self.analyze()
        self.assertEqual(result.status_code, 200)
        self.assertIn('Telegram rejected', ' '.join(logs.output))


if __name__ == '__main__':
    unittest.main()
