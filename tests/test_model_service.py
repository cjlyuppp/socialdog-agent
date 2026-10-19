"""Credential rotation, API authorization and secret isolation against the real app."""
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient

import test_api_server as fixtures
from socialdog_agent.server.app import create_app
from socialdog_agent.server.provider import DeepSeekCredentials, validate_deepseek_key
from socialdog_agent.server.store import APIError


KEY = 'integration-only-provider-key-one'
NEXT_KEY = 'integration-only-provider-key-two'


class ModelServiceTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.APITests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.APITests.tearDownClass.__func__)
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    new_user = fixtures.APITests.new_user
    settings = fixtures.APITests.settings
    session = fixtures.APITests.session
    finished = fixtures.APITests.finished

    def save(self, key=KEY):
        with patch('socialdog_agent.server.provider.validate_deepseek_key') as validate:
            result = self.client.put('/api/admin/model-settings', json={'api_key': key})
            validate.assert_called_once_with(key)
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def test_save_keeps_key_private_and_returns_only_status(self):
        response = self.save()
        self.assertEqual(set(response.json()), {'provider', 'configured', 'updated_at'})
        self.assertTrue(response.json()['configured'])
        path = self.app.state.credentials.path
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(path.read_text())['api_key'], KEY)
        self.assertEqual(self.app.state.credentials.read().key, KEY)
        self.assertNotIn(KEY, repr(self.app.state.credentials.read()))
        for endpoint in ('/api/admin/model-settings', '/api/admin/settings', '/api/models', '/api/me'):
            self.assertNotIn(KEY, self.client.get(endpoint).text)
        with self.store.connection() as db:
            self.assertNotIn(KEY, '\n'.join(db.iterdump()))

    def test_only_admin_with_csrf_may_read_test_replace_or_disable(self):
        reader, user_id, _ = self.new_user()
        self.client.patch('/api/admin/users/' + user_id, json={'permissions': ['review_feedback', 'review_ideas', 'view_researchers']})
        for client, expected in ((reader, 403), (TestClient(self.app, base_url='https://testserver'), 401)):
            for method, path, body in [('GET', '/api/admin/model-settings', None), ('PUT', '/api/admin/model-settings', {'api_key': KEY}), ('POST', '/api/admin/model-settings/check', None), ('DELETE', '/api/admin/model-settings', None)]:
                self.assertEqual(client.request(method, path, json=body).status_code, expected)
        csrf = self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.put('/api/admin/model-settings', json={'api_key': KEY}).status_code, 403)
        self.client.headers['X-CSRF-Token'] = csrf
        self.assertFalse(self.app.state.credentials.path.exists())

    def test_failed_validation_preserves_the_working_key_and_reports_no_secret(self):
        self.save()
        for key in ('', 'short', 'spaces in secret', 'line\nbreak', '中文密钥abcdefgh'):
            response = self.client.put('/api/admin/model-settings', json={'api_key': key})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(key if key else 'api_key', response.text)
        with patch('socialdog_agent.server.provider.validate_deepseek_key', side_effect=APIError(400, 'DeepSeek API Key 无效，请检查后重试。')):
            response = self.client.put('/api/admin/model-settings', json={'api_key': NEXT_KEY})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(NEXT_KEY, response.text)
        self.assertEqual(self.app.state.credentials.read().key, KEY)
        self.assertEqual(self.client.put('/api/admin/model-settings', json={'api_key': NEXT_KEY, 'base_url': 'https://example.invalid'}).status_code, 400)

    def test_disable_overrides_server_key_and_survives_restart(self):
        self.save()
        self.assertFalse(self.client.delete('/api/admin/model-settings').json()['configured'])
        reopened = DeepSeekCredentials(self.config.database.parent, 'server-fallback-key')
        self.assertEqual(reopened.read().key, '')
        self.assertFalse(reopened.status()['configured'])
        self.assertEqual(self.client.post('/api/admin/model-settings/check').status_code, 400)
        self.save(NEXT_KEY)
        self.assertEqual(reopened.read().key, NEXT_KEY)

    def test_connection_check_is_not_a_research_job_or_quota_charge(self):
        self.save()
        with patch('socialdog_agent.server.provider.validate_deepseek_key') as validate:
            response = self.client.post('/api/admin/model-settings/check')
            self.assertEqual(response.status_code, 200)
            validate.assert_called_once_with(KEY)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM model_calls').fetchone()[0], 0)

    def test_rotation_is_used_by_next_job_without_restart_and_never_written_to_job(self):
        config = replace(self.config, database=self.root / 'rotation' / 'workspace.sqlite', state_dir=self.root / 'rotation' / 'audit')
        env = {'DEEPSEEK_API_KEY': '', 'DEEPSEEK_MODEL': 'deepseek-flash', 'DEEPSEEK_TIMEOUT': '45', 'DEEPSEEK_REASONING_EFFORT': 'high'}
        with patch('chat_agent.load_settings', return_value=env):
            app = create_app(config)
        token, _ = app.state.store.bootstrap('rotation_admin', '管理员')
        with TestClient(app, base_url='https://testserver') as client, patch('socialdog_agent.server.provider.validate_deepseek_key'), patch('chat_agent.make_llm', side_effect=lambda settings: fixtures.RecordingLLM(self)) as factory:
            login = client.post('/api/auth/invite/accept', json={'token': token, 'password': fixtures.PASSWORD}).json()
            client.headers['X-CSRF-Token'] = login['csrf_token']
            session = client.post('/api/sessions').json()['id']
            self.assertEqual(client.post('/api/sessions/' + session + '/messages', json={'question': '数据范围'}).status_code, 503)
            self.assertEqual(client.get('/api/me').json()['quota']['used_today'], 0)
            for key in (KEY, NEXT_KEY):
                self.assertEqual(client.put('/api/admin/model-settings', json={'api_key': key}).status_code, 200)
                response = client.post('/api/sessions/' + session + '/messages', json={'question': '数据范围'})
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(self.finished(response.json()['job']['id'], client)['status'], 'ok')
                self.assertEqual(factory.call_args.args[0]['DEEPSEEK_API_KEY'], key)
            self.assertFalse(client.delete('/api/admin/model-settings').json()['configured'])
            self.assertEqual(client.post('/api/sessions/' + session + '/messages', json={'question': '数据范围'}).status_code, 503)
            self.assertEqual(client.get('/api/me').json()['quota']['used_today'], 2)
            with app.state.store.connection() as db:
                dump = '\n'.join(db.iterdump())
                self.assertNotIn(KEY, dump)
                self.assertNotIn(NEXT_KEY, dump)

    def test_server_configuration_is_adopted_until_overridden_and_corruption_fails_closed(self):
        credentials = DeepSeekCredentials(self.root / 'adoption', KEY)
        self.assertEqual(credentials.read().key, KEY)
        credentials.save(NEXT_KEY, 'actor')
        self.assertEqual(credentials.read().key, NEXT_KEY)
        credentials.path.write_text('invalid json')
        with self.assertRaises(APIError):
            credentials.read()
        credentials.save(KEY, 'actor')
        self.assertEqual(credentials.read().key, KEY)


class DeepSeekConnectionTests(unittest.TestCase):
    def test_metadata_probe_uses_fixed_endpoint_and_does_not_send_research_content(self):
        with patch('socialdog_agent.server.provider.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"object":"list","data":[{"id":"deepseek-flash"}]}'
            validate_deepseek_key(KEY)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.deepseek.com/models')
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + KEY)
            self.assertEqual(build.return_value.open.call_args.kwargs['timeout'], 10)

    def test_provider_failures_never_echo_raw_body_url_or_key(self):
        for error in (HTTPError('https://api.deepseek.com/models', 401, KEY, {}, io.BytesIO(KEY.encode())), URLError(KEY), HTTPError('https://example.invalid', 302, KEY, {}, io.BytesIO(KEY.encode()))):
            with patch('socialdog_agent.server.provider.build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(APIError) as caught:
                    validate_deepseek_key(KEY)
                self.assertNotIn(KEY, caught.exception.message)
                self.assertNotIn('example.invalid', caught.exception.message)
