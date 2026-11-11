"""Behaviour of the public HTTP API (all 13 endpoints).

These tests describe what the application does, not how it is built. They were written against
the procedural code and must keep passing after the object-oriented refactor.
"""

import json
import unittest
from unittest.mock import patch

from support import (DON_CHAT, MAN_CHAT, TELEGRAM_ERROR_GROUP, FakeAI, FakeTelegram, ReportedCase,
                     get_app, load_app, login, make_admin, make_clause, register, reset_database,
                     run_sql, upload)

USERNAME_RULES = "Username must be 3-30 characters using letters, numbers, '.', '_' or '-'."


class ApiCase(ReportedCase):
    detection = json.dumps([make_clause(),
                            make_clause(clause_type='Auto Renewal', risk_level='LOW',
                                        clause_text='The contract renews every year.',
                                        line_number='Page 4')])

    def setUp(self):
        reset_database()
        self.module = get_app()
        self.client = self.module.app.test_client()
        self.ai = FakeAI(detection=self.detection)
        self.telegram = FakeTelegram()
        for fake in (self.ai, self.telegram):
            context = fake.installed()
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)

    def signed_in_client(self, username='alice'):
        client = self.module.app.test_client()
        self.assertEqual(register(client, username).status_code, 201)
        return client

    def analyze_json(self, client=None, **kwargs):
        response = upload(client or self.client, **kwargs)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()


class AuthTests(ApiCase):
    def test_register(self):
        response = register(self.client, 'Alice', 'password123')
        self.verify('Register a new customer (username is lower-cased)',
                    "POST /api/auth/register {'username': 'Alice', 'password': 'password123'}",
                    (201, {'user': {'id': 'us_001', 'username': 'alice', 'role': 'customer'}}),
                    (response.status_code, response.get_json()))
        me = self.client.get('/api/auth/me')
        self.verify('Registering signs the user in',
                    'GET /api/auth/me with the session cookie',
                    (200, 'alice'), (me.status_code, me.get_json()['user']['username']))

    def test_register_validation(self):
        cases = [
            ('Username too short', {'username': 'ab', 'password': 'password123'}, 400, USERNAME_RULES),
            ('Password too short', {'username': 'alice', 'password': 'short'}, 400,
             'Password must be at least 8 characters.'),
        ]
        for title, body, status, message in cases:
            response = self.client.post('/api/auth/register', json=body)
            self.verify(f'Register rejected: {title}', f'POST /api/auth/register {body}',
                        (status, message), (response.status_code, response.get_json()['error']))
        register(self.client, 'alice')
        response = register(self.module.app.test_client(), 'ALICE')
        self.verify('Register rejected: username already taken (case-insensitive)',
                    "POST /api/auth/register {'username': 'ALICE'} after 'alice' exists",
                    (409, 'That username is already taken. Please choose another.'),
                    (response.status_code, response.get_json()['error']))

    def test_login_and_logout(self):
        register(self.client, 'alice')
        other = self.module.app.test_client()
        response = login(other, 'ALICE')
        self.verify('Login works with any letter case', "POST /api/auth/login {'username': 'ALICE'}",
                    (200, 'alice'), (response.status_code, response.get_json()['user']['username']))
        response = login(self.module.app.test_client(), 'alice', 'wrong-password')
        self.verify('Wrong password gives a generic message',
                    "POST /api/auth/login {'password': 'wrong-password'}",
                    (401, 'Incorrect username or password.'),
                    (response.status_code, response.get_json()['error']))
        response = self.module.app.test_client().post('/api/auth/login', json={'username': '', 'password': ''})
        self.verify('Empty login form is rejected', "POST /api/auth/login {'username': '', 'password': ''}",
                    (400, 'Please enter your username and password.'),
                    (response.status_code, response.get_json()['error']))
        other.post('/api/auth/logout')
        response = other.get('/api/auth/me')
        self.verify('After logout nobody is signed in', 'POST /api/auth/logout then GET /api/auth/me',
                    (401, 'Not signed in'), (response.status_code, response.get_json()['error']))

    def test_protected_routes_need_login(self):
        response = self.client.get('/api/analyses')
        self.verify('Protected route without login', 'GET /api/analyses (no session)',
                    (401, 'Please sign in to continue.'),
                    (response.status_code, response.get_json()['error']))


class AnalyzeTests(ApiCase):
    def setUp(self):
        super().setUp()
        register(self.client, 'alice')

    def test_successful_analysis(self):
        body = self.analyze_json(context='Our company is the supplier')
        first = body['clauses'][0]
        self.verify('Analysis response summary',
                    "POST /api/analyze: contract.txt + context 'Our company is the supplier' (AI returns 2 clauses)",
                    {'id': 'an_001', 'filename': 'contract.txt', 'context': 'Our company is the supplier',
                     'status': 'done', 'counts': {'high': 1, 'medium': 0, 'low': 1}, 'total_flagged': 2,
                     'cached': False},
                    {key: body[key] for key in ('id', 'filename', 'context', 'status', 'counts',
                                                'total_flagged', 'cached')})
        self.verify('First clause is stored and shaped for the frontend',
                    'clauses[0] of the response above',
                    {'id': 'cl_1', 'clause_type': 'Unlimited Liability', 'risk_level': 'HIGH',
                     'clause_text': 'The supplier is liable for all damages.',
                     'location': {'page': 2, 'clause_ref': '3(f)', 'raw': 'Page 2, Clause 3(f)'},
                     'issue_description': 'Liability has no cap', 'workaround': 'Negotiate a cap',
                     'legal_references': [], 'line_number': 'Page 2, Clause 3(f)'},
                    {key: first[key] for key in ('id', 'clause_type', 'risk_level', 'clause_text', 'location',
                                                 'issue_description', 'workaround', 'legal_references',
                                                 'line_number')})
        self.verify('AI was called twice (detect clauses, improve workarounds)',
                    'Fake AI call count', 2, len(self.ai.calls))

    def test_same_contract_is_cached(self):
        first = self.analyze_json()
        calls = len(self.ai.calls)
        second = self.analyze_json()
        self.verify('Same contract and context returns the saved analysis',
                    'POST /api/analyze twice with identical file and context',
                    {'id': first['id'], 'cached': True, 'new_ai_calls': 0},
                    {'id': second['id'], 'cached': second['cached'], 'new_ai_calls': len(self.ai.calls) - calls})
        third = self.analyze_json(context='another question')
        self.verify('A new context forces a new analysis', "Same file with context 'another question'",
                    {'id': 'an_002', 'cached': False}, {'id': third['id'], 'cached': third['cached']})

    def test_missing_and_bad_files(self):
        response = self.client.post('/api/analyze', data={}, content_type='multipart/form-data')
        self.verify('No file attached', 'POST /api/analyze without a file',
                    (400, 'No contract file provided'), (response.status_code, response.get_json()['error']))
        response = upload(self.client, text='   ')
        self.verify('File without text', 'POST /api/analyze with a blank .txt',
                    (400, 'Could not extract text from document'),
                    (response.status_code, response.get_json()['error']))
        response = upload(self.client, filename='notes.xyz')
        self.verify('Unsupported file type gives a generic error with a reference',
                    'POST /api/analyze with notes.xyz',
                    (500, True), (response.status_code, 'Reference:' in response.get_json()['error']))

    def test_risk_levels_and_defaults_are_normalised(self):
        self.ai.detection = json.dumps([{'clause_type': 'Odd', 'risk_level': 'critical',
                                         'clause_text': 'Some text'}])
        clause = self.analyze_json()['clauses'][0]
        self.verify('Unknown risk level becomes MEDIUM; missing fields get defaults',
                    "AI clause {'clause_type': 'Odd', 'risk_level': 'critical', 'clause_text': 'Some text'}",
                    ('MEDIUM', 'Potential risk identified', 'Consult legal counsel'),
                    (clause['risk_level'], clause['issue_description'], clause['workaround']))

    def test_ai_returning_no_json(self):
        self.ai.detection = 'Sorry, I cannot help'
        body = self.analyze_json()
        self.verify('AI answer without JSON means no clauses (not an error)',
                    "Fake AI answers 'Sorry, I cannot help'",
                    ('done', 0, 'No problematic clauses detected'),
                    (body['status'], body['total_flagged'], body['message']))
        again = self.analyze_json()
        self.verify('An empty result is never reused from the cache', 'Upload the same file again',
                    ('an_002', False), (again['id'], again['cached']))

    def test_legal_references_from_firecrawl(self):
        module = load_app({'FIRECRAWL_API_KEY': 'fc-test'})
        client = module.app.test_client()
        register(client, 'carol')
        results = {'data': [{'title': 'Smith v Jones', 'url': 'https://www.example.org/case/1',
                             'description': 'Court capped liability.'}]}
        with patch('firecrawl.FirecrawlApp.search', return_value=results):
            response = upload(client)
        clause = response.get_json()['clauses'][0]
        self.verify('Case law found by Firecrawl is saved and resolved on the clause',
                    "Firecrawl returns 1 result for 'Unlimited Liability'",
                    [{'title': 'Smith v Jones', 'url': 'https://www.example.org/case/1',
                      'summary': 'Court capped liability.', 'domain': 'example.org',
                      'verification': 'unverified'}],
                    clause['legal_references'])

    def test_ai_failure_alert_and_engineer_rejects(self):
        self.ai.always_fail = RuntimeError('503 provider down')
        self.telegram.button_presses = ['reject']
        response = upload(self.client)
        listing = self.client.get('/api/analyses').get_json()['analyses']
        texts = [message[2] for message in self.telegram.sent_to(TELEGRAM_ERROR_GROUP)]
        self.verify('All AI models failing and the engineer pressing Stop gives a 503',
                    "Every model raises '503 provider down'; Telegram button 'reject'",
                    (503, 'The AI service is currently unavailable. Please try again in a few minutes.', 'failed'),
                    (response.status_code, response.get_json()['error'], listing[0]['status']))
        self.verify('Every model is tried (2 attempts each) before alerting',
                    'Fake AI call count', 16, len(self.ai.calls))
        self.verify('Telegram group is alerted with Fix / Stop buttons, then told recovery was cancelled',
                    'Messages sent to the INF group',
                    ('⚠️ AI Processing Error\n\nAI processing error: 503 provider down',
                     ['accept', 'reject'], '🛑 Manual recovery cancelled.'),
                    (texts[0], [button['callback_data'] for row in self.telegram.messages[0][3]['inline_keyboard']
                                for button in row], texts[1]))

    def test_ai_failure_and_engineer_accepts_backup(self):
        self.ai.fail_first_calls = 16
        self.telegram.button_presses = ['accept']
        body = self.analyze_json()
        texts = [message[2] for message in self.telegram.sent_to(TELEGRAM_ERROR_GROUP)]
        self.verify('Pressing Fix retries with the backup model and the analysis succeeds',
                    "First 16 AI calls fail; Telegram button 'accept'",
                    ('done', 2), (body['status'], body['total_flagged']))
        self.verify('Group is told recovery started and succeeded (one alert only)',
                    'Messages sent to the INF group',
                    ['⚠️ AI Processing Error\n\nAI processing error: 503 provider down',
                     '🔄 Attempting manual AI recovery...', '✅ Backup AI successfully recovered!'],
                    texts)
        self.verify('Both pipeline stages used the approved backup model',
                    'Models used by the last 2 AI calls',
                    ['nvidia/nemotron-3-ultra-550b-a55b:free'] * 2, self.ai.models_tried[-2:])


class FlaggedClauseAlertTests(ApiCase):
    def setUp(self):
        super().setUp()
        register(self.client, 'alice')

    def flag(self, count, risk='HIGH'):
        self.ai.detection = json.dumps([make_clause(clause_type=f'Type {i}', clause_text=f'Text {i}',
                                                    risk_level=risk) for i in range(count)])
        return self.analyze_json(context=f'{count}-{risk}')

    def test_thresholds(self):
        for count, expected in ((3, 'nobody'), (6, 'nobody'), (7, 'don'), (8, 'don'), (9, 'man')):
            self.telegram.messages.clear()
            self.flag(count)
            sent = ('don' if self.telegram.sent_to(DON_CHAT) else
                    'man' if self.telegram.sent_to(MAN_CHAT) else 'nobody')
            self.verify(f'{count} flagged clauses notify: {expected}',
                        f'Analysis with {count} flagged clauses (7-8 -> medium chat, 9+ -> high chat)',
                        expected, sent)

    def test_message_content_and_cached_results(self):
        self.flag(9, risk='MEDIUM')
        message = self.telegram.sent_to(MAN_CHAT)[0][2]
        self.verify('Clause alert lists the user and the risk breakdown',
                    '9 MEDIUM clauses analysed by alice',
                    'Contract clause analysis completed\nUser ID: us_001\nUsername: alice\n'
                    'Total clause flagged: 9\nHigh risk: 0\nMedium Risk: 9\nLow risk: 0', message)
        self.telegram.messages.clear()
        upload(self.client, context='9-MEDIUM')
        self.verify('A cached result sends no second notification', 'Upload the same contract and context again',
                    0, len(self.telegram.messages))


class AnalysisHistoryTests(ApiCase):
    def setUp(self):
        super().setUp()
        register(self.client, 'alice')

    def test_list_filter_get_and_delete(self):
        first = self.analyze_json(context='one')
        self.ai.detection = json.dumps([make_clause(risk_level='LOW')])
        second = self.analyze_json(context='two')
        listing = self.client.get('/api/analyses').get_json()['analyses']
        self.verify('History lists newest first with summaries (no clauses or contract text)',
                    'GET /api/analyses',
                    ([second['id'], first['id']],
                     ['context', 'counts', 'filename', 'id', 'status', 'timestamp', 'total_flagged']),
                    ([a['id'] for a in listing], sorted(listing[0])))
        high = self.client.get('/api/analyses?risk=high').get_json()['analyses']
        self.verify('Risk filter keeps analyses with a HIGH clause', 'GET /api/analyses?risk=high',
                    [first['id']], [a['id'] for a in high])
        bad = self.client.get('/api/analyses?status=bogus')
        self.verify('Invalid status filter is rejected', 'GET /api/analyses?status=bogus',
                    (400, "Invalid status 'bogus'. Expected one of: processing, done, failed"),
                    (bad.status_code, bad.get_json()['error']))
        opened = self.client.get(f"/api/analyses/{first['id']}")
        self.verify('Reopen a saved analysis without calling the AI',
                    f"GET /api/analyses/{first['id']}",
                    (200, first['id'], first['clauses'], 'done'),
                    (opened.status_code, opened.get_json()['id'], opened.get_json()['clauses'],
                     opened.get_json()['status']))
        deleted = self.client.delete(f"/api/analyses/{first['id']}")
        gone = self.client.get(f"/api/analyses/{first['id']}")
        self.verify('Delete own analysis', f"DELETE /api/analyses/{first['id']}, then GET it",
                    (200, {'deleted': first['id']}, 404),
                    (deleted.status_code, deleted.get_json(), gone.status_code))

    def test_other_users_cannot_see_or_delete(self):
        mine = self.analyze_json()
        bob = self.signed_in_client('bob')
        results = (bob.get(f"/api/analyses/{mine['id']}").status_code,
                   bob.delete(f"/api/analyses/{mine['id']}").status_code,
                   bob.get('/api/analyses').get_json()['analyses'])
        self.verify("Bob cannot read, delete or list alice's analysis",
                    f"As bob: GET and DELETE /api/analyses/{mine['id']}, GET /api/analyses",
                    (404, 404, []), results)

    def test_admin_can_read_but_in_progress_cannot_be_deleted(self):
        mine = self.analyze_json()
        username, password = make_admin()
        admin = self.module.app.test_client()
        login(admin, username, password)
        read = admin.get(f"/api/analyses/{mine['id']}")
        self.verify("Admin can read any user's analysis", f"As admin: GET /api/analyses/{mine['id']}",
                    200, read.status_code)
        run_sql("INSERT INTO analyses (id, owner_id, status, filename, context, contract_text, contract_hash) "
                "VALUES ('an_800', 'us_001', 'processing', 'x.txt', '', 'text', 'hash')")
        busy = self.client.delete('/api/analyses/an_800')
        self.verify('An analysis that is still processing cannot be deleted',
                    'DELETE /api/analyses/an_800 (status processing)',
                    (409, 'This analysis is still in progress. Try again once it has finished.'),
                    (busy.status_code, busy.get_json()['error']))


class CompareTests(ApiCase):
    def setUp(self):
        super().setUp()
        register(self.client, 'alice')

    def two_analyses(self):
        self.ai.detection = json.dumps([
            make_clause(),
            make_clause(clause_type='Auto Renewal', risk_level='LOW', clause_text='Renews yearly.')])
        older = self.analyze_json(context='v1')
        self.ai.detection = json.dumps([
            make_clause(risk_level='LOW'),
            make_clause(clause_type='Termination', risk_level='MEDIUM', clause_text='Either side may end.')])
        newer = self.analyze_json(context='v2')
        return older, newer

    def test_compare(self):
        older, newer = self.two_analyses()
        body = self.client.get(f"/api/compare?a={older['id']}&b={newer['id']}").get_json()
        pair = body['in_both'][0]
        self.verify('Compare pairs the same clause and reports the risk change',
                    f"GET /api/compare?a={older['id']}&b={newer['id']} (HIGH liability -> LOW liability)",
                    ('text', 'lower', 'Unlimited Liability'),
                    (pair['matched_on'], pair['risk_change'], pair['a']['clause_type']))
        self.verify('Clauses that appear on one side only are listed separately',
                    'only_a / only_b clause types',
                    (['Auto Renewal'], ['Termination'], True),
                    ([c['clause_type'] for c in body['only_a']], [c['clause_type'] for c in body['only_b']],
                     body['same_contract']))
        self.verify('Each side carries its summary and owner name',
                    "body['a'] fields",
                    (older['id'], 'alice'), (body['a']['id'], body['a']['owner_username']))
        swapped = self.client.get(f"/api/compare?a={newer['id']}&b={older['id']}&chronological=1").get_json()
        self.verify('chronological=1 puts the older analysis first',
                    f"GET /api/compare?a={newer['id']}&b={older['id']}&chronological=1",
                    older['id'], swapped['a']['id'])

    def test_compare_errors(self):
        older, newer = self.two_analyses()
        run_sql("INSERT INTO analyses (id, owner_id, status, filename, context, contract_text, contract_hash) "
                "VALUES ('an_800', 'us_001', 'failed', 'x.txt', '', 'text', 'hash')")
        cases = [
            ('Both ids are required', '/api/compare?a=an_001', 400, 'Choose two analyses to compare.'),
            ('Two different analyses are required', '/api/compare?a=an_001&b=an_001', 400,
             'Choose two different analyses to compare.'),
            ('Unknown analysis', '/api/compare?a=an_001&b=an_999', 404, 'Analysis not found'),
            ('Only finished analyses', '/api/compare?a=an_001&b=an_800', 409,
             'Only finished analyses can be compared.'),
        ]
        for title, url, status, message in cases:
            response = self.client.get(url)
            self.verify(f'Compare error: {title}', f'GET {url}', (status, message),
                        (response.status_code, response.get_json()['error']))

    def test_compare_options(self):
        self.analyze_json(text='first contract', filename='contract.txt', context='v1')
        self.analyze_json(text='second contract', filename='other.txt', context='v1')
        current = self.analyze_json(text='third contract', filename='Contract.TXT', context='v2')
        body = self.client.get(f"/api/analyses/{current['id']}/compare-options").get_json()
        self.verify('Compare options split analyses of the same file from the rest',
                    f"GET /api/analyses/{current['id']}/compare-options",
                    (['an_001'], ['an_002']),
                    ([a['id'] for a in body['same_file']], [a['id'] for a in body['others']]))


class AdminTests(ApiCase):
    def test_admin_area(self):
        alice = self.signed_in_client('alice')
        upload(alice)
        denied = alice.get('/api/admin/users')
        self.verify('Customers are refused the admin area', 'As alice: GET /api/admin/users',
                    (403, 'This area is for administrators only.'),
                    (denied.status_code, denied.get_json()['error']))
        username, password = make_admin()
        admin = self.module.app.test_client()
        login(admin, username, password)
        users = admin.get('/api/admin/users').get_json()['users']
        self.verify('Admin lists every account with its analysis count', 'As admin: GET /api/admin/users',
                    [('admin', 'admin', 0), ('alice', 'customer', 1)],
                    [(u['username'], u['role'], u['analysis_count']) for u in users])
        listing = admin.get('/api/admin/analyses?user=us_001').get_json()['analyses']
        self.verify("Admin lists one user's analyses labelled with the owner", 'GET /api/admin/analyses?user=us_001',
                    [('an_001', 'alice')], [(a['id'], a['owner_username']) for a in listing])
        missing = admin.get('/api/admin/analyses?user=us_777')
        self.verify('Unknown user filter', 'GET /api/admin/analyses?user=us_777',
                    (404, 'User not found'), (missing.status_code, missing.get_json()['error']))

    def test_admin_seeded_from_environment(self):
        module = load_app({'ADMIN_USERNAME': 'Boss', 'ADMIN_PASSWORD': 'long-enough-1'})
        client = module.app.test_client()
        response = login(client, 'boss', 'long-enough-1')
        self.verify('ADMIN_USERNAME / ADMIN_PASSWORD create an admin at start-up',
                    "Start the app with ADMIN_USERNAME=Boss, then log in as 'boss'",
                    (200, 'admin'), (response.status_code, response.get_json()['user']['role']))

    def test_interrupted_analyses_are_failed_on_start(self):
        self.signed_in_client('alice')
        run_sql("INSERT INTO analyses (id, owner_id, status, filename, context, contract_text, contract_hash) "
                "VALUES ('an_800', 'us_001', 'processing', 'x.txt', '', 'text', 'hash')")
        load_app()
        status = run_sql("SELECT status FROM analyses WHERE id = 'an_800'").scalar()
        self.verify('An analysis left processing by a restart is marked failed on start-up',
                    "Row an_800 has status 'processing', then the app starts", 'failed', status)


class HealthTests(ApiCase):
    def test_health(self):
        body = self.client.get('/api/health').get_json()
        self.verify('Health check probes the top 4 models',
                    'GET /api/health (all models answer)',
                    ('healthy', True, False, 4),
                    (body['status'], body['openrouter_configured'], body['firecrawl_configured'],
                     len(body['working_models'])))
        self.ai.always_fail = RuntimeError('down')
        body = self.client.get('/api/health').get_json()
        self.verify('Health check reports degraded when no model answers',
                    'GET /api/health (every model raises)',
                    ('degraded', 4), (body['status'], len(body['failed_models'])))


if __name__ == '__main__':
    unittest.main()
