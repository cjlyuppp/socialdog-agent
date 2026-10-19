"""Configuration acceptance tests against real ASGI, SQLite and physical data packages."""
import json
import sqlite3
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

import test_api_server as fixtures
from socialdog_agent.server.app import create_app
from socialdog_agent.server.store import APIError, Store
from socialdog_agent.server.migrations import CURRENT_VERSION


SCORES = {'numbers': 'correct', 'reasoning': 5, 'definitions': 4, 'limitations': 3}


class ConfigurationTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.APITests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.APITests.tearDownClass.__func__)
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    new_user = fixtures.APITests.new_user
    settings = fixtures.APITests.settings
    session = fixtures.APITests.session
    ask = fixtures.APITests.ask
    finished = fixtures.APITests.finished

    def workspace(self):
        response = self.client.get('/api/admin/question-bank')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def publish(self, **patch):
        draft = {**self.workspace()['draft'], **patch}
        saved = self.client.put('/api/admin/question-bank/draft', json=draft)
        self.assertEqual(saved.status_code, 200, saved.text)
        response = self.client.post('/api/admin/question-bank/publish', json={'revision': saved.json()['draft']['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def evaluation(self, client, **patch):
        response = client.post('/api/eval/questions/q-data/run', json={'own_answer': '独立判断', **patch})
        self.assertEqual(response.status_code, 202, response.text)
        attempt = response.json()
        self.assertEqual(self.finished(attempt['job_id'], client)['status'], 'ok')
        return client.get('/api/eval/attempts/' + attempt['id']).json()

    def test_draft_edit_is_private_until_published_and_concurrent_edits_are_rejected(self):
        reader, _, _ = self.new_user()
        original = reader.get('/api/eval/questions').json()[0]
        draft = self.workspace()['draft']
        draft['questions'][0]['title'] = '管理员正式题'
        saved = self.client.put('/api/admin/question-bank/draft', json=draft)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(reader.get('/api/eval/questions').json()[0], original)
        self.assertEqual(self.client.put('/api/admin/question-bank/draft', json=draft).status_code, 409)
        self.assertEqual(self.client.post('/api/admin/question-bank/publish', json={'revision': draft['revision']}).status_code, 409)
        published = self.client.post('/api/admin/question-bank/publish', json={'revision': saved.json()['draft']['revision']})
        self.assertEqual(published.status_code, 200)
        current = reader.get('/api/eval/questions').json()[0]
        self.assertEqual(current['title'], '管理员正式题')
        self.assertNotEqual(current['bank_version_id'], original['bank_version_id'])
        self.assertEqual(self.workspace()['versions'][-1]['questions'][0]['title'], original['title'])

    def test_changed_T_generates_physical_cutoff_and_survives_restart(self):
        reader, _, _ = self.new_user()
        original = self.workspace()['active']
        current = self.publish(cutoff_T='2023-03-10', threshold_N=7)['active']
        self.assertNotEqual(original['dataset_version'], current['dataset_version'])
        self.assertEqual(reader.get('/api/status').json()['cutoff_date'], '2023-03-10')
        self.assertEqual(reader.get('/api/reference/query?by=all&start_date=2023-03-11').json()['rows'], [])
        self.assertLessEqual(reader.get('/api/reference/meta').json()['observation_end'], '2023-03-10')
        self.assertTrue(any(r['after_cutoff'] for r in self.client.get('/api/reference/query?by=all').json()['rows']))
        prepared = self.app.state.source.for_version(current['dataset_version'])
        with sqlite3.connect(prepared.package / 'history.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM lines WHERE order_date>? OR substr(payment_at,1,10)>?', ('2023-03-10', '2023-03-10')).fetchone()[0], 0)
        self.assertFalse((prepared.package / '原始文件_字节一致备份.xlsx').exists())
        submitted = self.ask(reader)
        self.assertEqual(self.finished(submitted['job']['id'], reader)['status'], 'ok')
        self.client.__exit__(None, None, None)
        self.opened = False
        restarted = create_app(self.config, llm_factory=lambda: fixtures.RecordingLLM(self))
        with TestClient(restarted, base_url='https://testserver') as client:
            client.cookies.update(reader.cookies)
            self.assertEqual(client.get('/api/status').json()['cutoff_date'], '2023-03-10')
            self.assertEqual(client.get('/api/eval/questions').json()[0]['bank_version_id'], current['id'])

    def test_old_assignment_uses_its_own_T_N_and_unassigned_old_version_is_denied(self):
        reader, user_id, _ = self.new_user()
        other, _, _ = self.new_user('another')
        old = self.workspace()['active']
        assigned = self.client.post('/api/admin/assignments', json={'user_id': user_id, 'question_id': 'q-data', 'bank_version_id': old['id']}).json()
        self.publish(cutoff_T='2023-03-10', threshold_N=7)
        self.assertEqual(other.post('/api/eval/questions/q-data/run', json={'own_answer': '答案', 'bank_version_id': old['id']}).status_code, 403)
        self.assertEqual(other.get('/api/reference/meta', params={'assignment_id': assigned['id']}).status_code, 404)
        self.assertEqual(other.get('/api/reference/query', params={'bank_version_id': old['id']}).status_code, 403)
        self.assertEqual(reader.get('/api/reference/meta', params={'assignment_id': assigned['id']}).json()['cutoff_date'], '2023-03-15')
        attempt = self.evaluation(reader, bank_version_id=old['id'], assignment_id=assigned['id'])
        with self.store.connection() as db:
            config = json.loads(db.execute('SELECT config FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0])
        self.assertEqual((config['dataset_version'], config['min_orders_per_group']), (old['dataset_version'], 5))

    def test_failed_publication_never_changes_active_scope_and_validates_questions(self):
        active = self.workspace()['active']
        for change in ({'questions': []}, {'cutoff_T': '2024-01-01'}):
            draft = {**self.workspace()['draft'], **change}
            saved = self.client.put('/api/admin/question-bank/draft', json=draft).json()['draft']
            self.assertEqual(self.client.post('/api/admin/question-bank/publish', json={'revision': saved['revision']}).status_code, 400)
            self.assertEqual(self.workspace()['active'], active)
        draft = self.workspace()['draft']
        draft['questions'] = [active['questions'][0], active['questions'][0]]
        self.assertEqual(self.client.put('/api/admin/question-bank/draft', json=draft).status_code, 400)

    def test_missing_archive_cannot_change_T(self):
        self.app.state.source.archive = None
        self.assertFalse(self.workspace()['can_change_cutoff'])
        saved = self.client.put('/api/admin/question-bank/draft', json={**self.workspace()['draft'], 'cutoff_T': '2023-03-10'}).json()
        response = self.client.post('/api/admin/question-bank/publish', json={'revision': saved['draft']['revision']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.workspace()['active']['cutoff_T'], '2023-03-15')

    def with_window(self):
        questions = self.workspace()['draft']['questions']
        questions[0]['post_cutoff_reference'] = {'start_date': '2023-03-17', 'end_date': '2023-03-20', 'product': '拿铁'}
        return self.publish(questions=questions)

    def test_post_submit_reference_is_owned_scoped_revocable_and_never_general(self):
        reader, _, _ = self.new_user()
        other, _, _ = self.new_user('another')
        self.with_window()
        self.settings(allow_post_cutoff_reference_after_submit=True)
        self.assertEqual(reader.get('/api/reference/query?by=all&start_date=2023-03-16').json()['rows'], [])
        attempt = self.evaluation(reader)
        params = {'by': 'day', 'scope': 'all', 'start_date': '2023-03-16', 'attempt_id': attempt['id']}
        rows = reader.get('/api/reference/query', params=params).json()['rows']
        self.assertTrue(rows)
        self.assertTrue(all('2023-03-17' <= r['group'] <= '2023-03-20' and r['after_cutoff'] for r in rows))
        self.assertEqual(reader.get('/api/reference/query', params={**params, 'product': '美式'}).json()['rows'], [])
        self.assertEqual(other.get('/api/reference/query', params=params).status_code, 404)
        self.assertEqual(other.get('/api/reference/meta', params={'attempt_id': attempt['id']}).status_code, 404)
        self.assertEqual(reader.get('/api/reference/query', params={'by': 'all', 'start_date': '2023-03-16'}).json()['rows'], [])
        self.assertEqual(reader.get('/api/reference/meta', params={'attempt_id': attempt['id']}).json()['observation_end'], '2023-03-20')
        # Compare exported query rows against the administrator's same product/window.
        expected = self.client.get('/api/reference/query', params={'scope': 'all', 'by': 'day', 'start_date': '2023-03-17', 'end_date': '2023-03-20', 'product': '拿铁'}).json()['rows']
        self.assertEqual(rows, expected)
        self.settings(allow_post_cutoff_reference_after_submit=False)
        self.assertEqual(reader.get('/api/reference/query', params=params).json()['rows'], [])

    def test_window_validation_prevents_unbounded_future_access(self):
        for window in ({'start_date': '2023-03-15', 'end_date': '2023-03-20'}, {'start_date': '2023-03-16', 'end_date': '2024-01-01'}):
            draft = self.workspace()['draft']
            draft['questions'][0]['post_cutoff_reference'] = window
            saved = self.client.put('/api/admin/question-bank/draft', json=draft).json()
            self.assertEqual(self.client.post('/api/admin/question-bank/publish', json={'revision': saved['draft']['revision']}).status_code, 400)

    def test_AB_mode_is_pinned_masks_provenance_maps_scores_and_reveals_after_rating(self):
        reader, _, _ = self.new_user()
        self.settings(anonymous_ab_evaluation=True)
        attempt = self.evaluation(reader)
        self.assertTrue(attempt['anonymous_ab'])
        self.assertIsNone(attempt['turn']['result'])
        self.assertEqual([p['label'] for p in attempt['answer_panels']], ['A', 'B'])
        self.assertTrue(all('source' not in p for p in attempt['answer_panels']))
        self.assertEqual(reader.post('/api/feedback', json={'source': 'evaluation', 'attempt_id': attempt['id'], 'turn_id': attempt['turn']['id'], 'stage': 'reasoning', 'description': '检查来源'}).status_code, 409)
        self.assertEqual(reader.post('/api/ratings', json={'attempt_id': attempt['id'], 'scores': SCORES}).status_code, 400)
        self.assertEqual(reader.post('/api/ratings', json={'attempt_id': attempt['id'], 'anonymous_scores': {'A': SCORES}}).status_code, 400)
        self.settings(anonymous_ab_evaluation=False)
        again = reader.get('/api/eval/attempts/' + attempt['id']).json()
        self.assertTrue(again['anonymous_ab'])
        other_scores = {**SCORES, 'reasoning': 1}
        scores = {'A': SCORES, 'B': other_scores}
        rated = reader.post('/api/ratings', json={'attempt_id': attempt['id'], 'anonymous_scores': scores})
        self.assertEqual(rated.status_code, 201, rated.text)
        revealed = reader.get('/api/eval/attempts/' + attempt['id']).json()
        agent_label = next(p['label'] for p in revealed['answer_panels'] if p['source'] == 'agent')
        self.assertEqual(rated.json()['scores'], scores[agent_label])
        self.assertEqual(rated.json()['anonymous_scores'], scores)
        self.assertIsNotNone(revealed['turn']['result'])
        self.assertEqual(reader.post('/api/ratings', json={'attempt_id': attempt['id'], 'anonymous_scores': scores}).status_code, 409)
        normal = self.evaluation(reader)
        self.assertFalse(normal['anonymous_ab'])
        self.assertIsNotNone(normal['turn']['result'])

    def test_reviewer_capabilities_are_separate_revocable_and_cannot_edit_configuration(self):
        reviewer, user_id, _ = self.new_user()
        for path in ('/api/admin/feedback', '/api/admin/ideas', '/api/review/members', '/api/admin/researchers/' + self.me['id']):
            self.assertEqual(reviewer.get(path).status_code, 403)
        self.client.patch('/api/admin/users/' + user_id, json={'permissions': ['review_feedback']})
        self.assertEqual(reviewer.get('/api/me').json()['permissions'], ['review_feedback'])
        self.assertEqual(reviewer.get('/api/admin/feedback').status_code, 200)
        self.assertEqual(reviewer.get('/api/admin/improvements/export.json').status_code, 200)
        members = reviewer.get('/api/review/members').json()
        self.assertTrue(all(set(m) == {'id', 'display_name'} for m in members))
        for path in ('/api/admin/ideas', '/api/admin/settings', '/api/admin/users', '/api/admin/question-bank', '/api/admin/ratings', '/api/admin/progress'):
            self.assertEqual(reviewer.get(path).status_code, 403)
        self.assertEqual(reviewer.patch('/api/admin/users/' + user_id, json={'role': 'admin'}).status_code, 403)
        self.assertEqual(reviewer.patch('/api/admin/research-policy', json={}).status_code, 403)
        self.client.patch('/api/admin/users/' + user_id, json={'permissions': ['review_ideas', 'view_researchers']})
        self.assertEqual(reviewer.get('/api/admin/feedback').status_code, 403)
        self.assertEqual(reviewer.get('/api/admin/ideas').status_code, 200)
        self.assertEqual(reviewer.get('/api/admin/researchers/' + self.me['id']).status_code, 200)
        self.client.patch('/api/admin/users/' + user_id, json={'permissions': []})
        self.assertEqual(reviewer.get('/api/admin/ideas').status_code, 403)

    def test_form_configuration_validates_server_side_and_preserves_submission_snapshot(self):
        reader, _, _ = self.new_user()
        policy = reader.get('/api/research-policy').json()
        policy['invitation_criteria'] = '研究问题明确，证据能复核'
        policy['idea_fields'][1].update(label='数据与样本', hint='请写明口径', required=False)
        saved = self.client.patch('/api/admin/research-policy', json=policy)
        self.assertEqual(saved.status_code, 200)
        form = {'question': '调价研究', 'findings': '相关性需要进一步验证', 'next_steps': '比较多个窗口'}
        created = reader.post('/api/ideas', json=form)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['form_snapshot'][1]['label'], '数据与样本')
        self.assertEqual(reader.post('/api/ideas', json={'question': '空表单'}).status_code, 400)
        policy['idea_fields'][1]['label'] = '新的标签'
        policy['invitation_criteria'] = '新标准'
        self.client.patch('/api/admin/research-policy', json=policy)
        old = reader.get('/api/ideas/mine').json()[0]
        self.assertEqual(old['form_snapshot'][1]['label'], '数据与样本')
        self.assertEqual(old['criteria_snapshot'], '研究问题明确，证据能复核')
        policy['idea_fields'][0]['required'] = False
        self.assertEqual(self.client.patch('/api/admin/research-policy', json=policy).status_code, 400)

    def test_account_limits_and_new_member_quota_defaults_are_configurable(self):
        self.settings(max_accounts=2, default_daily_limit=8, default_evaluation_reserve=3)
        created = self.client.post('/api/admin/users', json={'username': 'professor', 'display_name': '教授', 'permissions': ['review_ideas']})
        self.assertEqual(created.status_code, 201)
        user = created.json()['user']
        self.assertEqual((user['daily_limit'], user['evaluation_reserve']), (8, 3))
        self.assertEqual(user['permissions'], ['review_ideas'])
        self.assertEqual(self.client.post('/api/admin/users', json={'username': 'student', 'display_name': '学生'}).status_code, 409)
        self.settings(max_accounts=12, default_daily_limit=5, default_evaluation_reserve=2)
        self.assertEqual(next(u for u in self.client.get('/api/admin/users').json() if u['id'] == user['id'])['daily_limit'], 8)
        self.assertEqual(self.client.post('/api/admin/users', json={'username': 'student', 'display_name': '学生'}).status_code, 201)
        settings = self.client.get('/api/admin/settings').json()
        self.assertEqual(self.client.put('/api/admin/settings', json={**settings, 'max_accounts': 1}).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/settings', json={**settings, 'default_evaluation_reserve': 6}).status_code, 400)

    def test_old_conversation_data_is_not_reintroduced_after_lowering_T_or_changing_N(self):
        reader, _, _ = self.new_user()
        first = self.ask(reader)
        self.finished(first['job']['id'], reader)
        self.publish(cutoff_T='2023-03-10', threshold_N=9)
        self.requests.clear()
        response = reader.post('/api/sessions/' + first['job']['session_id'] + '/messages', json={'question': '继续描述数据范围'})
        self.assertEqual(response.status_code, 202)
        self.finished(response.json()['job']['id'], reader)
        # Old answers and their evidence scopes are excluded from the next model request.
        text = json.dumps(self.requests[0], ensure_ascii=False)
        self.assertNotIn('工具结果已核对', text)

    def test_stale_chat_configuration_is_rejected_before_reserving_quota(self):
        version = self.workspace()['active']
        self.publish(threshold_N=9)
        with self.assertRaises(APIError) as caught:
            self.store.submit(self.me['id'], '旧页面的问题', {'dataset_version': version['dataset_version'], 'bank_version_id': version['id']}, session_id=self.session())
        self.assertEqual(caught.exception.status, 409)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_v3_migration_preserves_existing_columns_and_is_repeatable(self):
        attempt = self.evaluation(self.client)
        self.client.post('/api/ratings', json={'attempt_id': attempt['id'], 'scores': SCORES})
        legacy_path = self.root / 'v3.sqlite'
        with self.store.connection() as db, sqlite3.connect(legacy_path) as legacy:
            for name in ('001_initial.sql', '002_reference_access.sql', '003_research_workflow.sql'):
                legacy.executescript((fixtures.Path('src/socialdog_agent/server/migrations') / name).read_text())
            tables = [r[0] for r in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            columns = {t: ','.join(r[1] for r in legacy.execute(f'PRAGMA table_info({t})')) for t in tables}
            before = {t: [tuple(r) for r in db.execute(f'SELECT {columns[t]} FROM {t} ORDER BY rowid')] for t in tables}
            for table in tables:
                legacy.execute(f'DELETE FROM {table}')
                for row in before[table]:
                    legacy.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in row)})', row)
            legacy.execute('PRAGMA user_version=3')
        upgraded = Store(legacy_path, '2023-03-15')
        Store(legacy_path, '2023-03-15')
        with upgraded.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], CURRENT_VERSION)
            self.assertEqual(before, {t: [tuple(r) for r in db.execute(f'SELECT {columns[t]} FROM {t} ORDER BY rowid')] for t in tables})

    def test_first_start_after_init_admin_adopts_loaded_package_cutoff(self):
        config = replace(self.config, database=self.root / 'bootstrap.sqlite', state_dir=self.root / 'bootstrap-audit')
        token, _ = Store(config.database, '1970-01-01').bootstrap('first_admin', '首位管理员')
        app = create_app(config, llm_factory=lambda: fixtures.RecordingLLM(self))
        with TestClient(app, base_url='https://testserver') as client:
            accepted = client.post('/api/auth/invite/accept', json={'token': token, 'password': fixtures.PASSWORD})
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(client.get('/api/status').json()['cutoff_date'], '2023-03-15')

    def test_runtime_settings_save_preserves_other_modules_and_enforces_authorization(self):
        stale = self.client.get('/api/admin/settings').json()
        self.publish(cutoff_T='2023-03-10', threshold_N=7)
        self.settings(invitation_criteria='新的审核标准')
        keys = ('site_daily_limit', 'input_price_per_million_yuan', 'output_price_per_million_yuan', 'max_concurrent_jobs', 'per_user_concurrent_jobs', 'max_accounts', 'default_daily_limit', 'default_evaluation_reserve')
        body = {k: stale[k] for k in keys}
        body['site_daily_limit'] = 80
        response = self.client.patch('/api/admin/settings', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()['cutoff_date'], response.json()['min_orders_per_group'], response.json()['invitation_criteria']), ('2023-03-10', 7, '新的审核标准'))
        reader, _, _ = self.new_user()
        self.assertEqual(reader.patch('/api/admin/settings', json=body).status_code, 403)
        self.assertEqual(reader.put('/api/admin/question-bank/draft', json=self.workspace()['draft']).status_code, 403)
        self.assertEqual(reader.post('/api/admin/question-bank/publish', json={'revision': 0}).status_code, 403)

    def test_authorized_reviewer_can_complete_reviews_without_member_management(self):
        reader, _, _ = self.new_user()
        reviewer, reviewer_id, _ = self.new_user('reviewer')
        submitted = self.ask(reader)
        self.finished(submitted['job']['id'], reader)
        issue = reader.post('/api/feedback', json={'source': 'chat', 'turn_id': submitted['turn']['id'], 'stage': 'reasoning', 'description': '应区分因果与相关'}).json()
        idea = reader.post('/api/ideas', json={'question': '研究问题', 'data_used': '订单', 'findings': '需控制趋势', 'next_steps': '多窗口比较'}).json()
        self.client.patch('/api/admin/users/' + reviewer_id, json={'permissions': ['review_feedback', 'review_ideas']})
        self.assertEqual(reviewer.patch('/api/admin/feedback/' + issue['id'], json={'review_status': 'confirmed', 'review_note': '反馈成立'}).status_code, 200)
        self.assertEqual(reviewer.patch('/api/admin/ideas/' + idea['id'], json={'status': 'feedback_given', 'reviewer_feedback': '请补充稳健性检查'}).status_code, 200)
        self.assertEqual(reader.get('/api/feedback/mine').json()[0]['reviewed_by'], reviewer_id)
        self.assertEqual(reader.get('/api/ideas/mine').json()[0]['reviewer_feedback'], '请补充稳健性检查')
        self.assertEqual(reviewer.post('/api/admin/users', json={'username': 'not_allowed', 'display_name': '无权限'}).status_code, 403)

    def test_multi_question_draft_supports_large_content_without_relaxing_single_question_limits(self):
        draft = self.workspace()['draft']
        question = draft['questions'][0]
        draft['questions'] = [{**question, 'id': f'q-{i}', 'question': '研究问题' * 500} for i in range(15)]
        response = self.client.put('/api/admin/question-bank/draft', json=draft)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['draft']['questions']), 15)
        self.assertEqual(self.client.post('/api/auth/login', content=b' ' * 65537).status_code, 413)
