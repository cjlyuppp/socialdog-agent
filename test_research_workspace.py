"""End-to-end domain checks against the real API, queue and SQLite store."""
import json
import unittest

from socialdog_agent.server.store import encoded
from socialdog_agent.server.workflow import Workflow
import test_api_server as fixtures


class WorkspaceTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.APITests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.APITests.tearDownClass.__func__)
    tearDown = fixtures.APITests.tearDown
    new_user = fixtures.APITests.new_user
    session = fixtures.APITests.session
    ask = fixtures.APITests.ask
    finished = fixtures.APITests.finished
    settings = fixtures.APITests.settings

    def setUp(self):
        fixtures.APITests.setUp(self)
        self.client.post('/api/notice/accept')

    def successful_turn(self, client):
        submitted = self.ask(client)
        self.assertEqual(self.finished(submitted['job']['id'], client)['status'], 'ok')
        session = client.get('/api/sessions/' + submitted['job']['session_id']).json()
        return session['turns'][0], session['id']

    def evaluation(self, client, **extra):
        response = client.post('/api/eval/questions/q-data/run', json={'own_answer': '独立答案，不传入模型', **extra})
        self.assertEqual(response.status_code, 202, response.text)
        self.finished(response.json()['job_id'], client)
        return client.get('/api/eval/attempts/' + response.json()['id']).json()

    def issue(self, turn, **extra):
        return {'source': 'chat', 'turn_id': turn['id'], 'stage': 'definition', 'description': '这里遗漏口径', 'correct_approach': '说明金额定义', 'evidence_refs': [turn['result']['evidence'][0]['call_id']], **extra}

    def test_chat_feedback_snapshot_is_immutable_and_review_context_complete(self):
        reader, user_id, _ = self.new_user()
        turn, _ = self.successful_turn(reader)
        created = reader.post('/api/feedback', json=self.issue(turn))
        self.assertEqual(created.status_code, 201, created.text)
        issue = created.json()
        with self.store.connection(write=True) as db:
            db.execute('UPDATE turns SET result=? WHERE id=?', (encoded({**turn['result'], 'answer': '后续内容', 'evidence': []}), turn['id']))
        queue = self.client.get('/api/admin/feedback', params={'user_id': user_id, 'source': 'chat', 'stage': 'definition', 'review_status': 'pending'}).json()
        self.assertEqual(queue[0]['model_answer'], turn['result']['answer'])
        self.assertEqual(queue[0]['evidence_snapshot'], turn['result']['evidence'])
        reviewed = self.client.patch('/api/admin/feedback/' + issue['id'], json={'review_status': 'confirmed', 'review_note': '应补充说明'})
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reader.get('/api/feedback/mine').json()[0]['review_note'], '应补充说明')
        exported = self.client.get('/api/admin/improvements/export.json')
        self.assertIn('attachment', exported.headers['content-disposition'])
        self.assertEqual(exported.json()[0]['id'], issue['id'])

    def test_feedback_rejects_foreign_answers_refs_sources_and_client_snapshots(self):
        reader, _, _ = self.new_user()
        other, _, _ = self.new_user('another')
        turn, _ = self.successful_turn(reader)
        self.assertEqual(other.post('/api/feedback', json=self.issue(turn)).status_code, 404)
        for patch in ({'evidence_refs': ['foreign']}, {'source': 'evaluation', 'attempt_id': 'foreign'}, {'evidence_snapshot': []}, {'description': '   '}):
            self.assertIn(reader.post('/api/feedback', json=self.issue(turn, **patch)).status_code, (400, 404))
        self.assertEqual(other.get('/api/feedback/mine').json(), [])

    def test_rating_and_multiple_issues_commit_once_and_atomically(self):
        reader, _, _ = self.new_user()
        attempt = self.evaluation(reader)
        draft = {'stage': 'calculation', 'description': '数字需核对', 'correct_approach': '', 'evidence_refs': []}
        rating = {'attempt_id': attempt['id'], 'scores': {'numbers': 'partial', 'reasoning': 3, 'definitions': 4, 'limitations': 4}, 'issues': [draft, {**draft, 'stage': 'reasoning', 'evidence_refs': ['foreign']}]}
        self.assertEqual(reader.post('/api/ratings', json=rating).status_code, 400)
        self.assertEqual(reader.get('/api/ratings/mine').json(), [])
        self.assertEqual(reader.get('/api/feedback/mine').json(), [])
        rating['issues'][1]['evidence_refs'] = []
        self.assertEqual(reader.post('/api/ratings', json=rating).status_code, 201)
        self.assertEqual(reader.post('/api/ratings', json=rating).status_code, 409)
        issues = reader.get('/api/feedback/mine').json()
        self.assertEqual(len(issues), 2)
        self.assertTrue(all(i['source'] == 'evaluation' and i['attempt_id'] == attempt['id'] for i in issues))

    def test_ideas_link_only_own_conversations_and_review_is_visible_to_owner(self):
        reader, _, _ = self.new_user()
        other, _, _ = self.new_user('another')
        session_id = self.session(reader)
        form = {'question': '价格和销量的关系', 'data_used': '历史订单', 'findings': '需控制商品差异', 'next_steps': '做分组比较', 'linked_conversations': [session_id]}
        self.assertEqual(other.post('/api/ideas', json=form).status_code, 404)
        created = reader.post('/api/ideas', json=form)
        self.assertEqual(created.status_code, 201)
        idea = created.json()
        self.assertEqual(idea['status'], 'pending')
        self.assertEqual(reader.patch('/api/admin/ideas/' + idea['id'], json={'status': 'invited'}).status_code, 403)
        for status in ('feedback_given', 'invited', 'not_suitable'):
            reviewed = self.client.patch('/api/admin/ideas/' + idea['id'], json={'status': status, 'reviewer_feedback': '请进一步界定样本'})
            self.assertEqual(reviewed.status_code, 200)
            mine = reader.get('/api/ideas/mine').json()[0]
            self.assertEqual((mine['status'], mine['reviewer_feedback']), (status, '请进一步界定样本'))
        self.assertEqual(other.get('/api/ideas/mine').json(), [])

    def assignment(self, user_id):
        question = self.client.get('/api/eval/questions').json()[0]
        response = self.client.post('/api/admin/assignments', json={'user_id': user_id, 'question_id': question['id'], 'bank_version_id': question['bank_version_id'], 'due_at': '2020-01-01'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_assignment_progress_tracks_saved_submissions_and_prevents_duplicate_assignment(self):
        reader, user_id, _ = self.new_user()
        assignment = self.assignment(user_id)
        mine = reader.get('/api/assignments/mine').json()[0]
        self.assertTrue(mine['overdue'])
        self.assertEqual(mine['id'], assignment['id'])
        duplicate = {k: assignment[k] for k in ('user_id', 'question_id', 'bank_version_id', 'due_at')}
        self.assertEqual(self.client.post('/api/admin/assignments', json=duplicate).status_code, 409)
        attempt = self.evaluation(reader, assignment_id=assignment['id'], bank_version_id=assignment['bank_version_id'])
        progress = self.client.get('/api/admin/progress').json()[0]
        self.assertEqual((progress['assigned'], progress['completed'], progress['overdue']), (1, 1, 0))
        self.assertEqual(reader.get('/api/assignments/mine').json()[0]['attempt_id'], attempt['id'])
        again = reader.post('/api/eval/questions/q-data/run', json={'own_answer': '再次提交', 'assignment_id': assignment['id'], 'bank_version_id': assignment['bank_version_id']})
        self.assertEqual(again.status_code, 409)

    def test_assignment_cannot_be_submitted_by_another_user_or_wrong_version(self):
        reader, user_id, _ = self.new_user()
        other, _, _ = self.new_user('another')
        assignment = self.assignment(user_id)
        self.assertEqual(other.get('/api/assignments/mine').json(), [])
        payload = {'own_answer': '答案', 'assignment_id': assignment['id'], 'bank_version_id': assignment['bank_version_id']}
        self.assertEqual(other.post('/api/eval/questions/q-data/run', json=payload).status_code, 404)
        self.settings(min_orders_per_group=9)
        latest = reader.get('/api/eval/questions').json()[0]
        self.assertEqual(reader.post('/api/eval/questions/q-data/run', json={**payload, 'bank_version_id': latest['bank_version_id']}).status_code, 404)

    def test_versions_pin_question_threshold_and_old_records_survive_changes(self):
        reader, user_id, _ = self.new_user()
        assignment = self.assignment(user_id)
        old_version = assignment['bank_version_id']
        self.settings(min_orders_per_group=9)
        new_version = reader.get('/api/eval/questions').json()[0]['bank_version_id']
        self.assertNotEqual(old_version, new_version)
        attempt = self.evaluation(reader, assignment_id=assignment['id'], bank_version_id=old_version)
        with self.store.connection() as db:
            config = json.loads(db.execute('SELECT config FROM jobs WHERE id=?', (attempt['job_id'],)).fetchone()[0])
        self.assertEqual(config['min_orders_per_group'], 5)
        self.assertEqual(attempt['bank_version_id'], old_version)
        changed = Workflow(self.store, self.app.state.source, {'q-data': {'id': 'q-data', 'category': 'quality', 'title': '新的标题', 'question': '新的问题'}})
        with self.store.connection(write=True) as db:
            changed.active_bank(db)
        self.assertEqual(reader.get('/api/eval/attempts/' + attempt['id']).json()['question_snapshot']['title'], '数据范围')
        self.assertGreaterEqual(len(self.client.get('/api/admin/question-bank/versions').json()), 3)
        bad = self.client.get('/api/admin/settings').json()
        self.assertEqual(self.client.put('/api/admin/settings', json={**bad, 'cutoff_date': '2023-03-31'}).status_code, 409)

    def test_model_failure_does_not_erase_independent_submission(self):
        reader, user_id, _ = self.new_user()
        assignment = self.assignment(user_id)
        self.model_fail = True
        attempt = self.evaluation(reader, assignment_id=assignment['id'], bank_version_id=assignment['bank_version_id'])
        self.assertEqual(attempt['turn']['status'], 'error')
        self.assertEqual(self.client.get('/api/admin/progress').json()[0]['completed'], 1)
        self.assertEqual(reader.get('/api/eval/attempts').json()[0]['own_answer'], '独立答案，不传入模型')

    def test_evaluation_reserve_remains_usable_after_chat_limit(self):
        reader, user_id, _ = self.new_user(daily_limit=2)
        self.assertEqual(self.client.patch('/api/admin/users/' + user_id, json={'evaluation_reserve': 1}).status_code, 200)
        self.successful_turn(reader)
        quota = reader.get('/api/me').json()['quota']
        self.assertEqual((quota['remaining_today'], quota['chat_remaining_today']), (1, 0))
        self.assertEqual(reader.post('/api/sessions/' + self.session(reader) + '/messages', json={'question': '继续研究'}).status_code, 429)
        self.evaluation(reader)
        self.assertEqual(reader.get('/api/me').json()['quota']['remaining_today'], 0)

    def test_reserve_validation_and_site_limit_still_apply_to_evaluation(self):
        reader, user_id, _ = self.new_user(daily_limit=3)
        for patch in ({'evaluation_reserve': 4}, {'evaluation_reserve': -1}, {'evaluation_reserve': True}):
            self.assertEqual(self.client.patch('/api/admin/users/' + user_id, json=patch).status_code, 400)
        self.client.patch('/api/admin/users/' + user_id, json={'evaluation_reserve': 2})
        self.assertEqual(self.client.patch('/api/admin/users/' + user_id, json={'daily_limit': 1}).status_code, 400)
        self.settings(site_daily_limit=0)
        self.assertEqual(reader.post('/api/eval/questions/q-data/run', json={'own_answer': '答案'}).status_code, 429)

    def test_notice_and_admin_conversation_read_are_role_and_owner_scoped(self):
        reader, user_id, _ = self.new_user()
        other, other_id, _ = self.new_user('another')
        turn, session_id = self.successful_turn(reader)
        url = f'/api/admin/researchers/{user_id}/sessions/{session_id}'
        self.assertEqual(reader.get('/api/me').json()['notice_version'], 0)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(reader.post('/api/notice/accept').json()['notice_version'], 1)
        self.assertEqual(self.client.get(url).json()['turns'][0]['id'], turn['id'])
        self.assertEqual(other.get(url).status_code, 403)
        self.assertEqual(self.client.get(f'/api/admin/researchers/{other_id}/sessions/{session_id}').status_code, 403)
        other.post('/api/notice/accept')
        self.assertEqual(self.client.get(f'/api/admin/researchers/{other_id}/sessions/{session_id}').status_code, 404)

    def test_admin_routes_exports_and_review_are_not_available_to_researchers(self):
        reader, user_id, _ = self.new_user()
        for path in ('/api/admin/feedback', '/api/admin/improvements/export.json', '/api/admin/ideas', '/api/admin/assignments', '/api/admin/progress', '/api/admin/question-bank/versions', f'/api/admin/researchers/{user_id}'):
            self.assertEqual(reader.get(path).status_code, 403, path)
        self.assertEqual(reader.post('/api/admin/assignments', json={'question_id': 'q-data', 'bank_version_id': 'x', 'user_id': user_id}).status_code, 403)

    def test_person_summary_counts_only_decided_feedback_and_labels_agent_ratings(self):
        reader, user_id, _ = self.new_user()
        turn, _ = self.successful_turn(reader)
        ids = [reader.post('/api/feedback', json=self.issue(turn, description=f'问题{i}')).json()['id'] for i in range(4)]
        for issue_id, status in zip(ids, ('confirmed', 'rejected', 'discuss', 'pending')):
            self.client.patch('/api/admin/feedback/' + issue_id, json={'review_status': status})
        summary = self.client.get('/api/admin/researchers/' + user_id).json()
        self.assertEqual((summary['question_count'], summary['feedback_count'], summary['confirmed_count'], summary['reviewed_count'], summary['confirmed_ratio']), (1, 4, 1, 2, .5))

    def test_legacy_attempt_backfill_keeps_unknown_metadata_unknown(self):
        reader, user_id, _ = self.new_user()
        attempt = self.evaluation(reader)
        with self.store.connection(write=True) as db:
            db.execute('UPDATE attempts SET bank_version_id=NULL WHERE id=?', (attempt['id'],))
            db.execute('UPDATE jobs SET config=? WHERE id=?', (encoded({'model': 'old-model', 'dataset_version': 'unknown'}), attempt['job_id']))
        Workflow(self.store, self.app.state.source, {})
        with self.store.connection() as db:
            row = db.execute('SELECT v.* FROM attempts a JOIN question_bank_version v ON v.id=a.bank_version_id WHERE a.id=?', (attempt['id'],)).fetchone()
        self.assertIsNone(row['cutoff_T'])
        self.assertIsNone(row['threshold_N'])
        self.assertEqual(row['legacy'], 1)

    def test_model_choices_reach_each_job_factory_without_cross_user_changes(self):
        reader, _, _ = self.new_user()
        other, _, _ = self.new_user('another')
        captured = []
        def factory(config):
            captured.append((config['model'], config['reasoning_effort']))
            return fixtures.RecordingLLM(self)
        self.app.state.runner.llm_factory = factory
        for client, model, effort in ((reader, 'deepseek-v4-pro', 'max'), (other, 'deepseek-flash', 'none')):
            response = client.post('/api/sessions/' + self.session(client) + '/messages', json={'question': '数据范围', 'model': model, 'reasoning_effort': effort})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.finished(response.json()['job']['id'], client)['status'], 'ok')
            turn = client.get('/api/sessions/' + response.json()['job']['session_id']).json()['turns'][0]
            self.assertEqual(turn['result']['reasoning_effort'], effort)
        self.assertEqual(captured, [('deepseek-v4-pro', 'max'), ('deepseek-flash', 'none')])
        catalog = reader.get('/api/models').json()
        self.assertEqual({m['id'] for m in catalog['models']}, {'deepseek-flash', 'deepseek-v4-pro'})

    def test_invalid_model_configuration_is_rejected_before_quota_or_calls(self):
        reader, user_id, _ = self.new_user()
        path = '/api/sessions/' + self.session(reader) + '/messages'
        for patch in ({'model': 'arbitrary'}, {'reasoning_effort': 'medium'}, {'reasoning_effort': 'ultra'}, {'api_key': 'forged'}, {'base_url': 'https://other.test'}):
            self.assertEqual(reader.post(path, json={'question': '问题', **patch}).status_code, 400)
        self.assertEqual(reader.get('/api/me').json()['quota']['used_today'], 0)
        self.assertEqual(len(self.requests), 0)

    def test_evaluation_records_model_choice_and_does_not_guess_other_model_cost(self):
        reader, user_id, _ = self.new_user()
        attempt = self.evaluation(reader, model='deepseek-v4-pro', reasoning_effort='low')
        self.assertEqual(attempt['turn']['result']['reasoning_effort'], 'low')
        report = self.client.get('/api/admin/usage', params={'month': fixtures.today()[:7]}).json()
        self.assertIsNone(report['total']['cost_yuan'])
        self.assertIsNotNone(report['total']['input_tokens'])

    def test_model_evidence_does_not_disclose_future_truncation_counts(self):
        reader, _, _ = self.new_user()
        turn, _ = self.successful_turn(reader)
        self.assertNotIn('source_rows_removed_after_as_of', json.dumps(turn['result']['evidence']))
        self.assertIn('source_rows_removed_after_as_of', self.app.state.source.description)

    def test_invalid_assignment_target_does_not_expire_administrator_session(self):
        reader, user_id, _ = self.new_user()
        question = self.client.get('/api/eval/questions').json()[0]
        form = {'user_id': 'missing', 'question_id': question['id'], 'bank_version_id': question['bank_version_id']}
        self.assertEqual(self.client.post('/api/admin/assignments', json=form).status_code, 404)
        self.client.patch('/api/admin/users/' + user_id, json={'disabled': True})
        self.assertEqual(self.client.post('/api/admin/assignments', json={**form, 'user_id': user_id}).status_code, 400)
        self.assertEqual(self.client.get('/api/me').status_code, 200)


if __name__ == '__main__':
    unittest.main()
