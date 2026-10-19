import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from socialdog_agent.agent import conversation_inputs
from socialdog_agent.llm import LLMError
from socialdog_agent.web import ChatService, LocalServer, SessionStore, WebError
import test_chat_agent as chat_tests
from test_chat_agent import ScriptedLLM, call, message, response
import web_agent


class ContextTest(unittest.TestCase):
    def test_loopback_binding_does_not_depend_on_reverse_dns(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = ChatService(Path(temporary), lambda: None, lambda: {})
            with patch("socket.getfqdn", side_effect=AssertionError("Loopback must not query DNS")):
                server = LocalServer(0, service)
            self.assertTrue(server.origin.startswith("http://127.0.0.1:"))
            server.server_close()

    def test_bounded_complete_turns_whitelist_and_recent_order(self):
        history = [{"question": str(i), "answer": "a" * 6000, "snapshot_versions": ["old"],
                    "query_scopes": [{"tool": "query_aggregate", "by": "all", "start_date": "2026-10-01",
                                      "end_date": "2026-10-03", "sql": "PRIVATE_SQL"}],
                    "evidence": "PRIVATE_RAW_ROWS"} for i in range(10)]
        inputs, turns = conversation_inputs(history)
        self.assertLessEqual(len(turns), 6)
        self.assertEqual(turns[-1]["question"], "9")
        self.assertEqual(len(inputs), 2 * len(turns))
        self.assertLessEqual(sum(len(json.dumps(turn, ensure_ascii=False)) for turn in turns), 24000)
        encoded = json.dumps(inputs)
        self.assertNotIn("PRIVATE_SQL", encoded)
        self.assertNotIn("PRIVATE_RAW_ROWS", encoded)
        self.assertIn("2026-10-01", encoded)


class ConversationAgentTest(unittest.TestCase):
    setUp = chat_tests.ResearchAgentTest.setUp
    tearDown = chat_tests.ResearchAgentTest.tearDown
    agent = chat_tests.ResearchAgentTest.agent
    events = chat_tests.ResearchAgentTest.events

    def test_history_is_context_and_new_question_still_requires_fresh_tools(self):
        llm = ScriptedLLM(response(call("describe_data", {})),
                          response(call("query_aggregate", {"by": "weekday", "start_date": "2026-10-01",
                                                            "end_date": "2026-10-01"}, "call_2")),
                          response(message("本轮已重新核验。")))
        events = []
        result = self.agent(llm).ask("再按星期比较", history=[{
            "question": "2026-10-01当天的订单数", "answer": "OLD_NUMBER_99999",
            "snapshot_versions": ["old_version"], "query_scopes": [{"tool": "query_aggregate",
                "by": "all", "start_date": "2026-10-01", "end_date": "2026-10-01"}]}], on_event=events.append)
        self.assertEqual(llm.requests[0]["tool_choice"], "required")
        self.assertEqual(llm.requests[0]["input"][-1]["content"], "再按星期比较")
        self.assertIn("old_version", llm.requests[0]["input"][1]["content"])
        self.assertEqual(result["snapshot_versions"], ["fixture_v1"])
        self.assertEqual(result["evidence"][1]["result"]["rows"][0]["system_net_yuan"], 210)
        self.assertIn("conversation_context", [event["event"] for event in self.events()])
        self.assertEqual(events[-1]["phase"], "complete")
        self.assertNotIn("PRIVATE_CUSTOMER", json.dumps(events))


class FakeAgent:
    def __init__(self):
        self.requests = []
        self.wait = threading.Event()
        self.wait.set()
        self.failure = None

    def ask(self, question, *, history, on_event):
        self.requests.append({"question": question, "history": copy.deepcopy(history)})
        on_event({"phase": "tool", "text": "正在查数", "tool": "query_aggregate"})
        self.wait.wait(3)
        if self.failure:
            raise self.failure
        return {"status": "ok", "answer": "<script>never execute</script>\n本轮工具结果。", "model": "fixture",
                "request_id": "fixture_request", "snapshot_versions": ["fixture_v1"],
                "statistical_tests_performed": 0, "evidence": [{"tool": "query_aggregate", "call_id": "c1",
                  "arguments": '{"by":"all","start_date":"2026-10-01","end_date":null}',
                  "result": {"status": "ok", "snapshot_version": "fixture_v1", "rows": []}}]}


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agent = FakeAgent()
        self.service = ChatService(self.root, lambda: self.agent,
                                   lambda: {"ready": True, "model": "fixture"})
        self.server = LocalServer(0, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.agent.wait.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        values = {"Origin": self.server.origin, "X-Socialdog-Token": self.service.token,
                  "Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, json.dumps(body) if body is not None else None, values)
        response = connection.getresponse()
        data = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, response_headers, json.loads(data) if "application/json" in response_headers["Content-Type"] else data

    def complete(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.service.job(job_id)
            if job["status"] != "running":
                return job
            time.sleep(.01)
        self.fail("job did not complete")

    def test_static_routes_do_not_expose_files_and_csp_blocks_inline_script(self):
        for route in ["/", "/app.js", "/styles.css"]:
            status, headers, _ = self.request("GET", route)
            self.assertEqual(status, 200)
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Cache-Control"], "no-store")
        for route in ["/.env", "/../.env", "/research_local/web/sessions.sqlite", "/api/settings"]:
            self.assertEqual(self.request("GET", route)[0], 404)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_origin_host_and_csrf_rejections_before_mutation(self):
        for headers in [{"Origin": "https://evil.example"}, {"Host": "evil.example"},
                        {"X-Socialdog-Token": "bad"}, {"Origin": ""},
                        {"Sec-Fetch-Site": "cross-site"}]:
            self.assertEqual(self.request("POST", "/api/sessions", {}, headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/sessions", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.service.store.list(), [])

    def test_session_roundtrip_history_isolation_progress_and_persistence(self):
        code, _, session = self.request("POST", "/api/sessions", {})
        self.assertEqual(code, 201)
        route = f"/api/sessions/{session['id']}/messages"
        code, _, started = self.request("POST", route, {"question": "第一问"})
        self.assertEqual(code, 202)
        result = self.complete(started["job_id"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["events"][0]["phase"], "tool")
        self.assertEqual(self.agent.requests[0]["history"], [])
        _, _, second = self.request("POST", route, {"question": "再按星期比较"})
        self.complete(second["job_id"])
        history = self.agent.requests[1]["history"]
        self.assertEqual(history[0]["query_scopes"][0]["start_date"], "2026-10-01")
        other = self.service.store.create()
        job = self.service.submit(other["id"], "别的会话")
        self.complete(job["job_id"])
        self.assertEqual(self.agent.requests[-1]["history"], [])
        restored = SessionStore(self.root).get(session["id"])
        self.assertEqual(len(restored["turns"]), 2)
        self.assertEqual(restored["title"], "第一问")
        self.assertEqual(self.service.store.path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_submit_rejected_and_failed_turn_not_used_as_context(self):
        session = self.service.store.create()
        self.agent.wait.clear()
        self.agent.failure = RuntimeError("PRIVATE_KEY_AND_LOCAL_PATH")
        job = self.service.submit(session["id"], "问题")
        with self.assertRaises(WebError) as caught:
            self.service.submit(session["id"], "重复")
        self.assertEqual(caught.exception.status, 409)
        self.agent.wait.set()
        result = self.complete(job["job_id"])
        self.assertNotIn("PRIVATE_KEY", json.dumps(result))
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.service.store.history(session["id"]), [])

    def test_interrupted_turn_becomes_explicit_error_after_restart(self):
        session = self.service.store.create()
        self.service.store.start(session["id"], "中断的问题", "a" * 32)
        restored = SessionStore(self.root).get(session["id"])
        self.assertEqual(restored["turns"][0]["status"], "error")
        self.assertIn("重新提问", restored["turns"][0]["error"])

    def test_failed_tool_parameters_are_not_reused_as_followup_scope(self):
        session = self.service.store.create()
        turn_id = "c" * 32
        self.service.store.start(session["id"], "查订单", turn_id)
        result = {"answer": "修正参数后的回答。", "snapshot_versions": ["v1"], "evidence": [
            {"tool": "query_aggregate", "arguments": '{"by":"hour","start_date":"INVALID"}',
             "result": {"status": "error"}},
            {"tool": "query_aggregate", "arguments": '{"by":"all","start_date":"2026-10-01"}',
             "result": {"status": "ok"}}]}
        self.service.store.finish(session["id"], turn_id, result=result)
        scopes = self.service.store.history(session["id"])[0]["query_scopes"]
        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0]["start_date"], "2026-10-01")

    def test_bad_questions_unknown_sessions_and_configuration_failures(self):
        session = self.service.store.create()
        route = f"/api/sessions/{session['id']}/messages"
        for question in ["", "a" * 4001, None, []]:
            self.assertEqual(self.request("POST", route, {"question": question})[0], 400)
        self.assertEqual(self.request("POST", "/api/sessions/" + "f" * 32 + "/messages", {"question": "x"})[0], 404)
        self.service.agent_factory = lambda: (_ for _ in ()).throw(LLMError("请配置API Key。"))
        self.assertEqual(self.request("POST", route, {"question": "x"})[0], 503)
        self.assertEqual(self.service.store.get(session["id"])["turns"], [])

    def test_status_error_never_exposes_file_path(self):
        self.service.status_provider = lambda: (_ for _ in ()).throw(OSError("PRIVATE_PATH"))
        status = self.request("GET", "/api/status")[2]
        self.assertFalse(status["ready"])
        self.assertNotIn("PRIVATE_PATH", json.dumps(status))

    def test_production_status_filters_api_key(self):
        class Source:
            def __init__(self, **kwargs):
                pass
            def resolve(self):
                return self.root if hasattr(self, "root") else Path("."), {"kind": "fixture"}
        with patch.object(web_agent, "FeishuSnapshotSource", Source), \
             patch.object(web_agent.chat_agent, "load_settings", return_value={
                 "DEEPSEEK_API_KEY": "PRIVATE_API_KEY", "DEEPSEEK_MODEL": "fixture", "DEEPSEEK_TIMEOUT": "45"}), \
             patch.object(web_agent.research, "read_snapshot", return_value=({"snapshot_version": "v1"}, type("Connection", (), {"close": lambda _: None})())):
            service = web_agent.make_service(self.root / "production-test")
            status = service.status()
        self.assertTrue(status["ready"])
        self.assertNotIn("PRIVATE_API_KEY", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
