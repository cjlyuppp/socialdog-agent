import copy
from datetime import datetime
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import chat_agent
import research_assistant as research
from socialdog_agent import DeepSeekLLM, ResearchAgent, ResponsesLLM, build_research_tools
from socialdog_agent.llm import LLMError, parse_response
from socialdog_agent.tools import public_result


def response(*items, status="completed"):
    return {"id": "resp_test", "model": "deepseek-flash", "status": status, "output": list(items)}


def call(name, arguments, call_id="call_1"):
    return {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
            "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}


def message(text="工具结果已核对。"):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


class ScriptedLLM(ResponsesLLM):
    """A deterministic protocol fixture, never a substitute for a real LLM."""
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def create_response(self, **request):
        self.requests.append(copy.deepcopy(request))
        return parse_response(next(self.responses))


class ResearchAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / "snapshot"
        self.state = self.root / "state"
        self.snapshot.mkdir()
        database = self.snapshot / "snapshot.sqlite"
        with sqlite3.connect(database) as connection:
            connection.executescript(research.SCHEMA)
            records = []
            for i in range(21):
                record = {"系统已支付": True, "下单时间": datetime(2026, 10, 1, 12),
                          "测试核实状态": "待店方确认", "本次_支付观察": 1,
                          "订单应付总额": 12, "已退金额": 2, "系统支付净额": 10,
                          "匿名顾客编号": "PRIVATE_CUSTOMER_" + str(i), "顾客标识类型": "会员",
                          "配送方式": "PICKUP", "销售来源": "PAD_POS",
                          "钱包支付额": 0, "优惠券减免": 0}
                if i == 20:
                    record["配送方式"] = "SUPPRESSED_GROUP"
                records.append(research.normalized_order(record, b"k" * 32))
            connection.executemany(research.INSERT, records)
        self.manifest = {"snapshot_version": "fixture_v1", "tool_version": research.TOOL_VERSION,
                         "database_sha256": research.digest(database), "source_sha256": "PRIVATE_SOURCE_HASH",
                         "source_file": "PRIVATE_SOURCE_FILE.xlsx", "storage": "SQLite",
                         "source_tables": [{"name": "订单数据", "row_count": 21, "fields": ["下单时间"]}],
                         "snapshot_as_of": "2026-10-01T23:59:59+08:00", "orders_in_source": 21,
                         "observation_start": "2026-10-01T12:00:00+08:00", "limitations": research.LIMITATIONS,
                         "tools": research.READINESS}
        (self.snapshot / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.tools = build_research_tools(self.snapshot, self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def agent(self, llm, **kwargs):
        return ResearchAgent(llm, self.tools, self.state, **kwargs)

    def events(self):
        return [json.loads(line) for line in (self.state / "agent/analysis_audit.jsonl").read_text().splitlines()]

    def test_responses_protocol_roundtrip_and_private_fields_stay_local(self):
        reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "protocol fixture"}]}
        llm = ScriptedLLM(response(reasoning, call("describe_data", {})),
                          response(call("query_aggregate", {"by": "all", "start_date": None,
                                                            "end_date": None}, "call_2")), response(message()))
        result = self.agent(llm).ask("订单数和净额是多少？")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["evidence"][0]["result"]["source_table_count"], 1)
        self.assertEqual(result["evidence"][1]["result"]["rows"][0]["system_net_yuan"], 210)
        self.assertEqual(result["snapshot_versions"], ["fixture_v1"])
        self.assertEqual(llm.requests[0]["tool_choice"], "required")
        self.assertIn(reasoning, llm.requests[1]["input"])
        for request in llm.requests[1:]:
            calls = {item["call_id"] for item in request["input"] if item.get("type") == "function_call"}
            outputs = {item["call_id"] for item in request["input"] if item.get("type") == "function_call_output"}
            self.assertEqual(calls, outputs)
            self.assertNotIn("previous_response_id", request)
        outbound = json.dumps(llm.requests)
        for sentinel in ["PRIVATE_CUSTOMER", "PRIVATE_SOURCE_HASH", "PRIVATE_SOURCE_FILE", "database_sha256"]:
            self.assertNotIn(sentinel, outbound)
        self.assertEqual([e["event"] for e in self.events()],
                         ["question", "model_response", "tool_result", "model_response", "tool_result", "model_response", "answer"])
        tool_audit = (self.state / "analysis_audit.jsonl").read_text().splitlines()
        self.assertEqual(len(tool_audit), 2)

    def test_parallel_calls_are_executed_and_paired_individually(self):
        llm = ScriptedLLM(response(call("describe_data", {}), call("check_analysis_readiness",
                          {"analysis": "stored_value_analysis"}, "call_2")), response(message()))
        result = self.agent(llm).ask("现有数据能分析储值吗？")
        self.assertEqual(result["evidence"][1]["result"]["status"], "unavailable")
        self.assertTrue(result["evidence"][1]["result"]["missing"])
        self.assertEqual(len([i for i in llm.requests[1]["input"] if i.get("type") == "function_call_output"]), 2)

    def test_small_group_does_not_reach_model(self):
        llm = ScriptedLLM(response(call("query_aggregate", {"by": "delivery", "start_date": None,
                                                         "end_date": None})), response(message()))
        result = self.agent(llm).ask("配送方式统计")
        self.assertEqual(result["evidence"][0]["result"]["rows"][0]["paid_observed_orders"], 20)
        self.assertNotIn("SUPPRESSED_GROUP", json.dumps(llm.requests))

    def test_unknown_sql_personal_and_malformed_arguments_rejected(self):
        attempts = [("execute_python", "{}"), ("describe_data", "[]"), ("describe_data", "invalid"),
                    ("query_aggregate", '{"sql":"SELECT * FROM orders"}'),
                    ("query_aggregate", '{"by":"all","start_date":null,"end_date":null,"customer_id":"x"}'),
                    ("query_aggregate", '{"by":"all","start_date":true,"end_date":null}'),
                    ("check_analysis_readiness", '{"analysis":[]}')]
        for name, arguments in attempts:
            with self.subTest(name=name, arguments=arguments):
                self.assertEqual(self.tools.execute(name, arguments, "test")["status"], "error")
        self.assertFalse((self.state / "analysis_audit.jsonl").exists())

    def test_invalid_attempt_is_audited_then_model_can_recover(self):
        llm = ScriptedLLM(response(call("execute_python", {})),
                          response(call("describe_data", {}, "call_2")), response(message()))
        result = self.agent(llm).ask("有什么数据？")
        self.assertEqual(result["evidence"][0]["result"]["status"], "error")
        self.assertEqual(llm.requests[1]["tool_choice"], "required")
        self.assertEqual(len([e for e in self.events() if e["event"] == "tool_result"]), 2)

    def test_no_answer_without_valid_tool_evidence(self):
        llm = ScriptedLLM(response(call("execute_python", {})), response(message("伪造结果")))
        with self.assertRaises(LLMError):
            self.agent(llm).ask("查询")
        self.assertEqual(self.events()[-1]["event"], "error")

    def test_round_and_tool_limits_stop_execution(self):
        llm = ScriptedLLM(response(call("describe_data", {})), response(call("describe_data", {}, "call_2")))
        with self.assertRaises(LLMError):
            self.agent(llm, max_rounds=2).ask("查询")
        llm = ScriptedLLM(response(call("describe_data", {}), call("describe_data", {}, "call_2")))
        with self.assertRaises(LLMError):
            self.agent(llm, max_tool_calls=1).ask("查询")
        self.assertEqual(len(llm.requests), 1)

    def test_duplicate_call_id_across_rounds_stops(self):
        llm = ScriptedLLM(response(call("describe_data", {})), response(call("describe_data", {})))
        with self.assertRaises(LLMError):
            self.agent(llm).ask("查询")
        self.assertEqual(len([e for e in self.events() if e["event"] == "tool_result"]), 1)

    def test_audit_failure_prevents_network_call(self):
        llm = ScriptedLLM()
        with patch("socialdog_agent.agent.research.record_audit", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.agent(llm).ask("查询")
        self.assertEqual(llm.requests, [])

    def test_local_file_error_details_do_not_reach_model(self):
        (self.snapshot / "snapshot.sqlite").unlink()
        result = self.tools.execute("describe_data", "{}", "数据清单")
        self.assertEqual(result["status"], "error")
        self.assertNotIn(str(self.snapshot), json.dumps(result))
        self.assertNotIn("snapshot.sqlite", json.dumps(result))
        local_audit = (self.state / "analysis_audit.jsonl").read_text()
        self.assertIn("snapshot.sqlite", local_audit)


class ResponsesClientTest(unittest.TestCase):
    def test_identity_coverage_explicitly_refers_to_all_source_orders(self):
        result = public_result({"status": "ok", "source_tables": [], "identity_coverage": {
            "missing_customer_id_orders": 968}})
        coverage = result["identity_coverage"]
        self.assertNotIn("missing_customer_id_orders", coverage)
        self.assertEqual(coverage["missing_customer_id_orders_in_all_source_orders"], 968)
        self.assertIn("全部来源订单", coverage["population"])

    def test_http_request_uses_flat_responses_format(self):
        client = DeepSeekLLM(api_key="TEST_ONLY_KEY")
        body = json.dumps(response(message())).encode()
        with patch.object(client._opener, "open", return_value=io.BytesIO(body)) as send:
            result = client.create_response(input=[{"role": "user", "content": "hi"}],
                          instructions="test", tools=[], tool_choice="required")
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/responses")
        self.assertEqual(payload["reasoning"], {"effort": "none"})
        self.assertFalse(payload["store"])
        self.assertNotIn("messages", payload)
        self.assertNotIn("TEST_ONLY_KEY", request.data.decode())
        self.assertEqual(result.output_text, "工具结果已核对。")

    def test_http_network_and_bad_json_errors_do_not_echo_credentials(self):
        client = DeepSeekLLM(api_key="TEST_ONLY_KEY")
        errors = [HTTPError(client.endpoint, 401, "TEST_ONLY_KEY", {}, None),
                  HTTPError(client.endpoint, 429, "TEST_ONLY_KEY", {}, None), URLError("TEST_ONLY_KEY")]
        for error in errors:
            with patch.object(client._opener, "open", side_effect=error):
                with self.assertRaises(LLMError) as caught:
                    client.create_response(input=[], instructions="", tools=[])
                self.assertNotIn("TEST_ONLY_KEY", str(caught.exception))
        with patch.object(client._opener, "open", return_value=io.BytesIO(b"not json")):
            with self.assertRaises(LLMError):
                client.create_response(input=[], instructions="", tools=[])

    def test_incomplete_and_malformed_outputs_rejected(self):
        bad_responses = [response(message(), status="incomplete"), response({"type": "computer_call"}),
                         response({"type": "function_call", "name": "x"}),
                         response(call("describe_data", {}), call("describe_data", {})),
                         response({"type": "message", "content": "invalid"})]
        for data in bad_responses:
            with self.assertRaises(LLMError):
                parse_response(data)

    def test_missing_key_and_invalid_timeout_rejected(self):
        for options in [{"api_key": ""}, {"api_key": "key\r\n"},
                        {"api_key": "key", "timeout": float("nan")}, {"api_key": "key", "timeout": 0}]:
            with self.assertRaises(LLMError):
                DeepSeekLLM(**options)

    def test_env_loading_is_data_only_and_environment_takes_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('DEEPSEEK_API_KEY="$(do-not-execute)"\nDEEPSEEK_MODEL=local-model\n')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(chat_agent.load_settings(path)["DEEPSEEK_API_KEY"], "$(do-not-execute)")
            with patch.dict(os.environ, {"DEEPSEEK_MODEL": "environment-model"}, clear=True):
                self.assertEqual(chat_agent.load_settings(path)["DEEPSEEK_MODEL"], "environment-model")
            path.write_text("export DEEPSEEK_API_KEY=bad\n")
            with self.assertRaises(LLMError):
                chat_agent.load_settings(path)


if __name__ == "__main__":
    unittest.main()
