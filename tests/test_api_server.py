"""Real ASGI/SQLite integration tests; only the external model is scripted."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

import historical_backtest as h
from socialdog_agent import LLMResponse, ResponsesLLM
from socialdog_agent.llm import LLMError
from socialdog_agent.server.app import ServerConfig, create_app
from socialdog_agent.server.data import AgentData, ReferenceData, build_reference
from socialdog_agent.server.store import Store, digest, today
from socialdog_agent.server.migrations import CURRENT_VERSION, migrate
from test_chat_agent import call, message
from test_historical_backtest import history_rows, write_history_workbook

PASSWORD = "only-for-integration-tests"


class RecordingLLM(ResponsesLLM):
    def __init__(self, owner):
        self.owner, self.round = owner, 0

    def create_response(self, **request):
        self.owner.requests.append(request)
        self.owner.entered.set()
        if not self.owner.gate.wait(5):
            raise LLMError("测试请求等待超时。")
        self.round += 1
        if self.owner.model_fail and self.round == 2:
            raise LLMError("模型请求失败，请重试。")
        content = call("history_describe", {}) if self.round == 1 else message("工具结果已核对。")
        if self.owner.repair and self.round == 2:
            content = message("营业额为987654321元。")
        usage = {} if self.owner.missing_usage else {"input_tokens": 10 * self.round, "output_tokens": 2 * self.round}
        return LLMResponse(id=f"response_{self.round}", model="test-model", output=[content], usage=usage)


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.data_tmp.name)
        source = cls.data / "source.xlsx"
        write_history_workbook(source)
        with redirect_stdout(io.StringIO()):
            h.build(source, cls.data / "full")
            h.build(source, cls.data / "cut", "2023-03-15")
        build_reference(cls.data / "full", cls.data / "reference.sqlite", "2023-03-15")
        bank = [{"id": "q-data", "category": "quality", "title": "数据范围", "question": "数据覆盖什么时间？", "reference_hint": {"end_date": "2023-03-15"}}]
        (cls.data / "questions.json").write_text(json.dumps(bank), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.data_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.requests, self.model_fail, self.repair, self.missing_usage = [], False, False, False
        self.gate, self.entered = threading.Event(), threading.Event()
        self.gate.set()
        self.config = ServerConfig(self.root / "workspace.sqlite", self.root / "audit", public_origin="https://testserver",
                                   history_package=self.data / "cut", reference_db=self.data / "reference.sqlite",
                                   question_bank=self.data / "questions.json")
        self.app = create_app(self.config, llm_factory=lambda: RecordingLLM(self), model_name="test-model")
        self.store = self.app.state.store
        token, _ = self.store.bootstrap("admin", "管理员")
        self.client = TestClient(self.app, base_url="https://testserver")
        self.client.__enter__()
        self.opened = True
        response = self.client.post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        self.me = response.json()
        self.client.headers["X-CSRF-Token"] = self.me["csrf_token"]
        self.others = []
        self.settings(input_price_per_million_yuan=2, output_price_per_million_yuan=8)

    def tearDown(self):
        self.gate.set()
        for other in self.others:
            other.close()
        if self.opened:
            self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def new_user(self, name="researcher", daily_limit=30):
        response = self.client.post("/api/admin/users", json={"username": name, "display_name": name, "role": "researcher", "daily_limit": daily_limit})
        self.assertEqual(response.status_code, 201, response.text)
        record = response.json()
        token = record["invite_url"].rsplit("/", 1)[1]
        other = TestClient(self.app, base_url="https://testserver")
        self.others.append(other)
        accepted = other.post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        other.headers["X-CSRF-Token"] = accepted.json()["csrf_token"]
        return other, record["user"]["id"], token

    def session(self, client=None):
        response = (client or self.client).post("/api/sessions")
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def ask(self, client=None, question="请描述数据范围。"):
        client = client or self.client
        response = client.post(f"/api/sessions/{self.session(client)}/messages", json={"question": question})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def finished(self, job_id, client=None):
        client = client or self.client
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get(f"/api/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] in ("ok", "error"):
                return job
            threading.Event().wait(0.02)
        self.fail("任务没有在测试期限内完成")

    def settings(self, **patch):
        settings = self.client.get("/api/admin/settings").json()
        response = self.client.put("/api/admin/settings", json={**settings, **patch})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_secure_cookie_opaque_session_and_password_hash(self):
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"].lower()
        for value in ("httponly", "secure", "samesite=lax"):
            self.assertIn(value, cookie)
        token = self.client.cookies.get("socialdog_session")
        with self.store.connection() as db:
            session = db.execute("SELECT token_hash FROM auth_sessions WHERE token_hash=?", (digest(token),)).fetchone()
            password_hash = db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        self.assertIsNotNone(session)
        self.assertNotEqual(password_hash, PASSWORD)
        self.assertTrue(password_hash.startswith("$argon2"))
        self.assertNotIn("password_hash", encoded_response := json.dumps(self.client.get("/api/admin/users").json()))
        self.assertNotIn("invite_hash", encoded_response)

    def test_unauthenticated_csrf_origin_and_host_boundaries(self):
        anonymous = TestClient(self.app, base_url="https://testserver")
        self.others.append(anonymous)
        self.assertEqual(anonymous.get("/api/sessions").status_code, 401)
        self.assertEqual(self.client.post("/api/sessions", headers={"X-CSRF-Token": "forged"}).status_code, 403)
        self.assertEqual(self.client.post("/api/sessions", headers={"Origin": "https://attacker.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/login", headers={"Origin": "https://attacker.example"}, json={"username": "admin", "password": PASSWORD}).status_code, 403)
        self.assertEqual(self.client.get("/api/me", headers={"Host": "attacker.example"}).status_code, 400)
        self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_invite_is_single_use_and_reinvite_revokes_sessions(self):
        other, user_id, old_token = self.new_user()
        self.assertEqual(other.post("/api/auth/invite/accept", json={"token": old_token, "password": PASSWORD}).status_code, 404)
        response = self.client.post(f"/api/admin/users/{user_id}/invite")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(other.get("/api/me").status_code, 401)
        self.assertEqual(other.post("/api/auth/login", json={"username": "researcher", "password": PASSWORD}).status_code, 401)

    def test_users_cannot_cross_read_sessions_jobs_or_attempts(self):
        other, _, _ = self.new_user()
        result = self.ask()
        job = result["job"]
        self.assertEqual(other.get(f"/api/jobs/{job['id']}").status_code, 404)
        self.assertEqual(other.get(f"/api/sessions/{job['session_id']}").status_code, 404)
        self.assertEqual(other.post(f"/api/sessions/{job['session_id']}/messages", json={"question": "不属于我的对话"}).status_code, 404)
        self.assertEqual(other.get("/api/sessions").json(), [])
        attempt = self.client.post("/api/eval/questions/q-data/run", json={"own_answer": "答案"})
        if attempt.status_code == 409:
            self.finished(job["id"])
            attempt = self.client.post("/api/eval/questions/q-data/run", json={"own_answer": "答案"})
        self.assertEqual(attempt.status_code, 202)
        self.assertEqual(other.get("/api/eval/attempts/" + attempt.json()["id"]).status_code, 404)

    def test_admin_routes_and_self_protection(self):
        other, _, _ = self.new_user()
        for path in ("users", "settings", "ratings", "usage?month=" + today()[:7]):
            self.assertEqual(other.get("/api/admin/" + path).status_code, 403)
        for patch in ({"disabled": True}, {"role": "researcher"}):
            self.assertEqual(self.client.patch("/api/admin/users/" + self.me["id"], json=patch).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/users/" + self.me["id"] + "/invite").status_code, 409)

    def test_complete_question_saves_real_evidence_and_per_call_usage(self):
        submitted = self.ask()
        job = self.finished(submitted["job"]["id"])
        self.assertEqual(job["status"], "ok", job)
        detail = self.client.get("/api/sessions/" + job["session_id"]).json()
        result = detail["turns"][0]["result"]
        self.assertEqual(result["usage"], {"input_tokens": 30, "output_tokens": 6})
        self.assertEqual(result["evidence"][0]["tool"], "history_describe")
        self.assertEqual(result["evidence"][0]["result"]["as_of"], "2023-03-15")
        self.assertEqual(self.client.get("/api/me").json()["quota"]["used_today"], 1)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM model_calls WHERE status='ok'").fetchone()[0], 2)
        usage = self.client.get("/api/admin/usage?month=" + today()[:7]).json()
        self.assertEqual(usage["total"], {"questions": 1, "input_tokens": 30, "output_tokens": 6, "cost_yuan": 0.000108})

    def test_repair_call_is_also_charged_and_recorded(self):
        self.repair = True
        submitted = self.ask()
        job = self.finished(submitted["job"]["id"])
        result = self.client.get("/api/sessions/" + job["session_id"]).json()["turns"][0]["result"]
        self.assertTrue(result["grounding"]["repair_attempted"])
        self.assertEqual(result["usage"], {"input_tokens": 60, "output_tokens": 12})

    def test_failed_call_preserves_prior_usage_and_marks_unknown_accounting(self):
        self.model_fail = True
        submitted = self.ask()
        self.assertEqual(self.finished(submitted["job"]["id"])["status"], "error")
        with self.store.connection() as db:
            rows = db.execute("SELECT status,input_tokens FROM model_calls ORDER BY rowid").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("ok", 10), ("error", None)])
        usage = self.client.get("/api/admin/usage?month=" + today()[:7]).json()["total"]
        self.assertEqual(usage["questions"], 1)
        self.assertIsNone(usage["input_tokens"])
        self.assertIsNone(usage["cost_yuan"])

    def test_missing_provider_usage_is_not_zero(self):
        self.missing_usage = True
        submitted = self.ask()
        self.assertEqual(self.finished(submitted["job"]["id"])["status"], "ok")
        result = self.client.get("/api/sessions/" + submitted["job"]["session_id"]).json()["turns"][0]["result"]
        self.assertEqual(result["usage"], {"input_tokens": None, "output_tokens": None})

    def test_partial_reference_hints_preserve_defaults_and_unknown_price_is_not_zero(self):
        question = self.client.get("/api/eval/questions").json()[0]
        self.assertEqual(question["reference_hint"], {"end_date": "2023-03-15"})
        self.settings(input_price_per_million_yuan=None, output_price_per_million_yuan=None)
        report = self.client.get("/api/admin/usage?month=" + today()[:7]).json()
        self.assertIsNone(report["total"]["cost_yuan"])

    def test_per_user_and_site_quota_reject_before_model(self):
        other, _, _ = self.new_user(daily_limit=0)
        response = other.post(f"/api/sessions/{self.session(other)}/messages", json={"question": "查询"})
        self.assertEqual(response.status_code, 429)
        self.settings(site_daily_limit=0)
        response = self.client.post(f"/api/sessions/{self.session()}/messages", json={"question": "查询"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.requests, [])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_one_active_job_and_global_queue(self):
        self.gate.clear()
        self.settings(max_concurrent_jobs=1)
        other, _, _ = self.new_user()
        first = self.ask()
        self.assertTrue(self.entered.wait(3))
        second = self.client.post(f"/api/sessions/{first['job']['session_id']}/messages", json={"question": "第二问"})
        self.assertEqual(second.status_code, 409)
        queued = self.ask(other)["job"]
        self.assertEqual(other.get("/api/jobs/" + queued["id"]).json()["status"], "queued")
        self.assertEqual(other.get("/api/jobs/" + queued["id"]).json()["queue_position"], 1)
        self.gate.set()
        self.assertEqual(self.finished(first["job"]["id"])["status"], "ok")
        self.assertEqual(self.finished(queued["id"], other)["status"], "ok")

    def test_quota_reservation_is_atomic_under_simultaneous_requests(self):
        self.gate.clear()
        other, _, _ = self.new_user()
        self.settings(site_daily_limit=1)
        first_session, second_session = self.session(), self.session(other)
        barrier = threading.Barrier(2)
        def send(client, session):
            barrier.wait()
            return client.post(f"/api/sessions/{session}/messages", json={"question": "查询"}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(send, self.client, first_session)
            second = pool.submit(send, other, second_session)
            self.assertEqual(sorted([first.result(), second.result()]), [202, 429])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_reference_only_reads_standalone_full_database_and_splits_mixed_groups(self):
        response = self.client.get("/api/reference/query?scope=baseline&by=all")
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["rows"]
        self.assertEqual([(r["order_count"], r["after_cutoff"]) for r in rows], [(90, False), (96, True)])
        product = self.client.get("/api/reference/query?scope=baseline&by=product").json()
        self.assertEqual(len(product["rows"]), 4)
        self.assertEqual(self.app.state.reference.path.name, "reference.sqlite")
        self.assertEqual(self.requests, [])
        with self.assertRaises(sqlite3.OperationalError):
            from socialdog_agent.server.data import readonly
            with readonly(self.data / "reference.sqlite") as db:
                db.execute("DELETE FROM lines")

    def test_reference_is_never_registered_as_agent_tool(self):
        tools = self.app.state.source.tools(self.root / "audit", {"min_orders_per_group": 5, "dataset_version": self.app.state.source.description["dataset_version"]})
        names = {item["name"] for item in tools.definitions()}
        self.assertEqual(names, {"history_describe", "history_aggregate", "history_price_events", "history_quality", "history_unavailable"})
        self.assertEqual(tools.execute("reference_query", "{}", "查询未来数据")["status"], "error")
        future = tools.execute("history_aggregate", json.dumps({"scope": "all", "by": "all", "start_date": "2023-03-16", "end_date": None}), "查询未来数据")
        self.assertEqual(future["rows"], [])
        self.assertFalse(hasattr(self.app.state.source, "reference"))

    def test_researcher_reference_enforces_cutoff_for_queries_and_exports(self):
        researcher, _, _ = self.new_user()
        meta = researcher.get('/api/reference/meta').json()
        self.assertFalse(meta['can_view_after_cutoff'])
        self.assertLessEqual(meta['observation_end'], '2023-03-15')
        for by in ('all', 'day', 'week', 'month', 'product', 'weekday'):
            with self.subTest(by=by):
                response = researcher.get('/api/reference/query', params={'scope': 'all', 'by': by, 'end_date': '2023-03-31'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['rows'])
                self.assertTrue(all(not row['after_cutoff'] for row in response.json()['rows']))
        for scope in ('baseline', 'all'):
            response = researcher.get('/api/reference/query', params={'scope': scope, 'by': 'all', 'start_date': '2023-03-16'})
            self.assertEqual(response.json()['rows'], [])
        self.assertEqual(researcher.get('/api/reference/query?by=all').json()['rows'][0]['order_count'], 90)
        self.assertEqual(researcher.get('/api/admin/ratings/export.csv').status_code, 403)
        self.assertEqual(researcher.get('/api/reference/query?can_view_after_cutoff=true').status_code, 400)
        self.assertTrue(self.client.get('/api/reference/meta').json()['can_view_after_cutoff'])
        self.assertEqual(len(self.client.get('/api/reference/query?by=all').json()['rows']), 2)

    def test_future_only_product_names_are_not_in_researcher_metadata(self):
        path = self.root / 'reference.sqlite'
        with sqlite3.connect(self.data / 'reference.sqlite') as source, sqlite3.connect(path) as destination:
            source.backup(destination)
            destination.execute("UPDATE lines SET product='FUTURE_ONLY_SENTINEL' WHERE order_date='2023-03-31'")
        reference = ReferenceData(path, '2023-03-15')
        self.assertNotIn('FUTURE_ONLY_SENTINEL', reference.metadata()['products'])
        self.assertIn('FUTURE_ONLY_SENTINEL', reference.metadata(can_view_after_cutoff=True)['products'])

    def test_evaluation_submission_does_not_unlock_future_reference(self):
        researcher, _, _ = self.new_user()
        attempt = researcher.post('/api/eval/questions/q-data/run', json={'own_answer': '独立答案'}).json()
        self.finished(attempt['job_id'], researcher)
        self.assertEqual(researcher.get('/api/reference/query?by=all&start_date=2023-03-16').json()['rows'], [])
        self.assertFalse(self.client.get('/api/admin/settings').json()['allow_post_cutoff_reference_after_submit'])
        settings = self.client.get('/api/admin/settings').json()
        self.assertEqual(self.client.put('/api/admin/settings', json={**settings, 'allow_post_cutoff_reference_after_submit': True}).status_code, 200)
        # Enabling the policy alone never unlocks questions without an explicit window.
        self.assertEqual(researcher.get('/api/reference/query', params={'by': 'all', 'start_date': '2023-03-16', 'attempt_id': attempt['id']}).json()['rows'], [])

    def test_v1_migration_preserves_existing_records_and_is_idempotent(self):
        attempt = self.client.post('/api/eval/questions/q-data/run', json={'own_answer': '旧答案'}).json()
        self.finished(attempt['job_id'])
        self.client.post('/api/ratings', json={'attempt_id': attempt['id'], 'scores': {'numbers': 'correct', 'reasoning': 4, 'definitions': 4, 'limitations': 4}, 'note': '旧反馈'})
        with self.store.connection(write=True) as db:
            old_settings = self.store.settings(db)
            old_settings.pop('allow_post_cutoff_reference_after_submit')
            old_settings.pop('anonymous_ab_evaluation')
            legacy_path = self.root / 'legacy.sqlite'
            with sqlite3.connect(legacy_path) as legacy:
                legacy.executescript(Path('src/socialdog_agent/server/migrations/001_initial.sql').read_text())
                tables = [r[0] for r in legacy.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='settings'")]
                columns = {t: ','.join(r[1] for r in legacy.execute(f'PRAGMA table_info({t})')) for t in tables}
                before = {t: [tuple(r) for r in db.execute(f'SELECT {columns[t]} FROM {t} ORDER BY rowid')] for t in tables}
                for table in tables:
                    for row in before[table]:
                        legacy.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in row)})', row)
                legacy.execute('INSERT INTO settings VALUES (1,?)', (json.dumps(old_settings),))
                legacy.execute('PRAGMA user_version=1')
        upgraded = Store(legacy_path, '2023-03-15')
        Store(legacy_path, '2023-03-15')
        with upgraded.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], CURRENT_VERSION)
            self.assertEqual(upgraded.settings(db), {**old_settings, 'allow_post_cutoff_reference_after_submit': False, 'anonymous_ab_evaluation': False})
            self.assertEqual(before, {table: [tuple(r) for r in db.execute(f'SELECT {columns[table]} FROM {table} ORDER BY rowid')] for table in tables})

    def test_failed_or_unknown_migration_does_not_advance_version(self):
        path = self.root / 'bad.sqlite'
        with sqlite3.connect(path, isolation_level=None) as db:
            db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO settings VALUES (1,'invalid json')")
            db.execute('PRAGMA user_version=1')
            with self.assertRaises(sqlite3.OperationalError):
                migrate(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT value FROM settings').fetchone()[0], 'invalid json')
            db.execute('PRAGMA user_version=999')
            with self.assertRaises(ValueError):
                migrate(db)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 999)

    def test_full_package_is_rejected_for_agent_and_cut_package_for_reference(self):
        with self.assertRaises(ValueError):
            AgentData(self.data / "full")
        with self.assertRaises(ValueError):
            build_reference(self.data / "cut", self.root / "reference.sqlite", "2023-03-15")
        with self.assertRaises(ValueError):
            ReferenceData(self.data / "full" / "history.sqlite", "2023-03-15")

    def test_invalid_reference_parameters_do_not_execute(self):
        for suffix in ("by=all;DROP%20TABLE%20lines", "start_date=2023-03-25&end_date=2023-03-01", "scope=unknown", "start_date=2023-02-30"):
            response = self.client.get("/api/reference/query?" + suffix)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(set(response.json()), {"error"})

    def test_evaluation_own_answer_stays_out_of_model_context_and_rating_once(self):
        self.assertEqual(self.client.post("/api/eval/questions/q-data/run", json={"own_answer": " "}).status_code, 400)
        attempt = self.client.post("/api/eval/questions/q-data/run", json={"own_answer": "PRIVATE_OWN_ANSWER_SENTINEL"}).json()
        self.finished(attempt["job_id"])
        self.assertNotIn("PRIVATE_OWN_ANSWER_SENTINEL", json.dumps(self.requests))
        scores = {"numbers": "not_applicable", "reasoning": 4, "definitions": 5, "limitations": 3}
        body = {"attempt_id": attempt["id"], "scores": scores, "note": "核对完成"}
        self.assertEqual(self.client.post("/api/ratings", json=body).status_code, 201)
        self.assertEqual(self.client.post("/api/ratings", json=body).status_code, 409)
        self.assertTrue(self.client.get("/api/eval/attempts/" + attempt["id"]).json()["rated"])
        self.assertEqual(self.client.get("/api/ratings/mine").json()[0]["scores"], scores)

    def test_rating_rejected_while_running_or_after_failure(self):
        self.gate.clear()
        attempt = self.client.post("/api/eval/questions/q-data/run", json={"own_answer": "答案"}).json()
        body = {"attempt_id": attempt["id"], "scores": {"numbers": "correct", "reasoning": 5, "definitions": 5, "limitations": 5}, "note": ""}
        self.assertEqual(self.client.post("/api/ratings", json=body).status_code, 409)
        self.model_fail = True
        self.gate.set()
        self.assertEqual(self.finished(attempt["job_id"])["status"], "error")
        self.assertEqual(self.client.post("/api/ratings", json=body).status_code, 409)

    def test_restart_preserves_history_and_marks_unfinished_jobs_error(self):
        first = self.ask()
        self.finished(first["job"]["id"])
        cookies = self.client.cookies
        version = self.client.get('/api/eval/questions').json()[0]['bank_version_id']
        self.client.__exit__(None, None, None)
        self.opened = False
        stale = self.store.submit(self.me["id"], "未执行问题", {"dataset_version": self.app.state.source.description["dataset_version"], "bank_version_id": version}, session_id=first["job"]["session_id"])
        self.app = create_app(self.config, llm_factory=lambda: RecordingLLM(self))
        self.client = TestClient(self.app, base_url="https://testserver")
        self.client.cookies.update(cookies)
        self.client.__enter__()
        self.opened = True
        job = self.client.get("/api/jobs/" + stale["job"]["id"]).json()
        self.assertEqual(job["status"], "error")
        detail = self.client.get("/api/sessions/" + first["job"]["session_id"]).json()
        self.assertEqual([t["status"] for t in detail["turns"]], ["ok", "error"])

    def test_second_server_cannot_recover_live_tasks(self):
        second = create_app(self.config, llm_factory=lambda: RecordingLLM(self))
        with self.assertRaises(RuntimeError):
            with TestClient(second, base_url="https://testserver"):
                pass
        self.assertEqual(self.client.get("/api/me").status_code, 200)

    def test_settings_persist_validate_and_do_not_change_cutoff_without_data(self):
        self.settings(min_orders_per_group=7, max_concurrent_jobs=2)
        self.assertEqual(self.client.get("/api/status").json()["min_orders_per_group"], 7)
        body = self.client.get("/api/admin/settings").json()
        for patch in ({"min_orders_per_group": 0}, {"per_user_concurrent_jobs": 2}, {"site_daily_limit": -1}):
            self.assertEqual(self.client.put("/api/admin/settings", json={**body, **patch}).status_code, 400)
        self.app.state.source.archive = None
        self.assertEqual(self.client.put("/api/admin/settings", json={**body, "cutoff_date": "2023-03-20"}).status_code, 409)
        self.assertEqual(self.client.get('/api/status').json()['cutoff_date'], '2023-03-15')

    def test_disabled_member_loses_access(self):
        other, user_id, _ = self.new_user()
        self.assertEqual(self.client.patch("/api/admin/users/" + user_id, json={"disabled": True}).status_code, 200)
        self.assertEqual(other.get("/api/me").status_code, 401)

    def test_user_limit_duplicates_and_validation(self):
        for n in range(9):
            response = self.client.post("/api/admin/users", json={"username": f"user_{n}", "display_name": f"用户{n}", "role": "researcher", "daily_limit": 1})
            self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.post("/api/admin/users", json={"username": "overflow", "display_name": "超限", "role": "researcher", "daily_limit": 1}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/users", json={"username": "user_0", "display_name": "重复", "role": "researcher", "daily_limit": 1}).status_code, 409)
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "PRIVATE_PASSWORD", "extra": True})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("PRIVATE_PASSWORD", response.text)

    def test_no_synthetic_questions_when_bank_is_not_configured(self):
        from dataclasses import replace
        app = create_app(replace(self.config, database=self.root / "empty.sqlite", question_bank=None), llm_factory=lambda: RecordingLLM(self))
        token, _ = app.state.store.bootstrap("owner", "管理员")
        with TestClient(app, base_url="https://testserver") as client:
            self.assertEqual(client.post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD}).status_code, 200)
            self.assertEqual(client.get("/api/eval/questions").json(), [])

    def test_exported_openapi_matches_actual_routes_and_models(self):
        expected = json.loads((Path(__file__).resolve().parents[1] / 'docs' / 'openapi.json').read_text())
        actual = self.app.openapi()
        self.assertEqual(actual['paths'], expected['paths'])
        self.assertEqual(actual['components']['schemas'], expected['components']['schemas'])

    def test_missing_key_refuses_before_reserving_quota(self):
        from dataclasses import replace
        from unittest.mock import patch
        with patch('chat_agent.load_settings', return_value={'DEEPSEEK_MODEL': 'test-model', 'DEEPSEEK_API_KEY': '', 'DEEPSEEK_REASONING_EFFORT': 'low'}):
            app = create_app(replace(self.config, database=self.root / 'unconfigured.sqlite'))
        token, _ = app.state.store.bootstrap('owner', '管理员')
        with TestClient(app, base_url='https://testserver') as client:
            me = client.post('/api/auth/invite/accept', json={'token': token, 'password': PASSWORD}).json()
            client.headers['X-CSRF-Token'] = me['csrf_token']
            session = client.post('/api/sessions').json()
            self.assertEqual(client.post(f"/api/sessions/{session['id']}/messages", json={'question': '查询'}).status_code, 503)
            self.assertEqual(client.get('/api/me').json()['quota']['used_today'], 0)

    def test_ratings_csv_preserves_bom_and_escapes_spreadsheet_formulas(self):
        attempt = self.client.post('/api/eval/questions/q-data/run', json={'own_answer': '=HYPERLINK("https://example.invalid")'}).json()
        self.finished(attempt['job_id'])
        scores = {'numbers': 'not_applicable', 'reasoning': 4, 'definitions': 4, 'limitations': 4}
        self.assertEqual(self.client.post('/api/ratings', json={'attempt_id': attempt['id'], 'scores': scores, 'note': '+unsafe'}).status_code, 201)
        result = self.client.get('/api/admin/ratings/export.csv')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content.startswith(b'\xef\xbb\xbf'))
        import csv
        rows = list(csv.reader(io.StringIO(result.text.lstrip('\ufeff'))))
        self.assertTrue(rows[1][3].startswith("'="))
        self.assertEqual(rows[1][-1], "'+unsafe")


if __name__ == "__main__":
    unittest.main()
