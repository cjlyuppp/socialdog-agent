from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import research_assistant as research
from socialdog_agent import ResearchAgent, build_research_tools
from socialdog_agent.factors import inspect_factors, to_markdown
from test_chat_agent import ScriptedLLM, response, call, message
from test_research_assistant import row


class FactorInspectionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(research.SCHEMA)
        self.manifest = {"source_tables": [
            {"name": "订单数据", "row_count": 21, "fields": ["下单时间", "配送方式", "销售来源",
             "优惠券减免", "钱包支付额", "匿名顾客编号", "积分抵扣", "取餐完成来源"]},
            {"name": "商品明细", "row_count": 21, "fields": ["历史商品名称", "数量", "关联订单"]},
            {"name": "商品价格快照", "row_count": 1, "fields": ["目录售价"]}],
            "snapshot_as_of": "2026-10-10T16:38:13+08:00", "observation_start": "2026-09-12T20:23:06+08:00",
            "last_order_at": "2026-10-10T16:33:03+08:00", "as_of_day_is_partial": True, "orders_in_source": 21}
        records = [row(str(i)) for i in range(20)] + [row("secret", 配送方式="PRIVATE_SMALL_GROUP")]
        self.connection.executemany(research.INSERT, [research.normalized_order(r, b"k" * 32) for r in records])
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def inspect(self, **kwargs):
        return inspect_factors(self.manifest, self.connection, **kwargs)

    def test_current_fields_give_four_descriptive_checks_and_no_effect_ranking(self):
        result = self.inspect()
        self.assertEqual(result["factor_count"], 13)
        self.assertEqual(result["readiness_counts"], {"descriptive_ready": 4, "missing_data": 8, "tool_available": 1,
                                                    "no_publishable_groups": 0, "tool_not_implemented": 0})
        self.assertEqual(result["statistical_tests_performed"], 0)
        self.assertFalse(result["trend_assessed"])
        self.assertEqual(result["data_overview"]["observation_calendar_days"], 29)
        self.assertTrue(all(f["importance_rank"] is None and f["causal_status"] == "not_estimated" for f in result["factors"]))
        hour = next(f for f in result["factors"] if f["id"] == "hour")
        self.assertEqual(hour["query_evidence"]["result"]["rows"][0]["paid_observed_orders"], 21)

    def test_price_snapshot_wallet_and_completion_label_do_not_satisfy_missing_data(self):
        factors = {f["id"]: f for f in self.inspect()["factors"]}
        self.assertIn("真实价格执行记录", factors["price"]["missing_data"])
        self.assertIn("充值本金", factors["stored_value"]["missing_data"])
        self.assertIn("实际出杯或完成时间", factors["waiting"]["missing_data"])
        self.assertEqual(factors["weather"]["readiness"], "missing_data")
        self.assertEqual(factors["product_mix"]["readiness"], "tool_available")
        self.assertEqual(factors["product_mix"]["use_tool"], "query_products")

    def test_suppressed_group_does_not_leak_through_factor_evidence(self):
        result = self.inspect()
        self.assertNotIn("PRIVATE_SMALL_GROUP", json.dumps(result))
        delivery = next(f for f in result["factors"] if f["id"] == "delivery")
        self.assertEqual(len(delivery["query_evidence"]["result"]["rows"]), 1)
        self.assertFalse(delivery["can_compare_released_groups"])

    def test_date_filters_apply_and_no_published_groups_are_not_treated_as_zero(self):
        result = self.inspect(target="sales", start_date="2026-10-02", end_date="2026-10-02")
        self.assertEqual(result["readiness_counts"]["descriptive_ready"], 0)
        self.assertEqual(result["readiness_counts"]["no_publishable_groups"], 4)
        for factor in result["factors"]:
            if "query_evidence" in factor:
                self.assertEqual(factor["query_evidence"]["parameters"]["start_date"], "2026-10-02")
                self.assertEqual(factor["query_evidence"]["result"]["rows"], [])

    def test_retention_target_does_not_claim_current_order_frequency_analysis(self):
        result = self.inspect(target="retention")
        self.assertEqual(result["factor_count"], 6)
        self.assertTrue(all("query_evidence" not in f for f in result["factors"]))
        self.assertEqual(result["readiness_counts"]["descriptive_ready"], 0)

    def test_empty_table_with_headers_is_not_data_ready(self):
        self.manifest["source_tables"][1]["row_count"] = 0
        product = next(f for f in self.inspect()["factors"] if f["id"] == "product_mix")
        self.assertEqual(product["readiness"], "missing_data")
        self.assertEqual(product["schema_evidence"][0]["presence"], "present_but_empty")

    def test_user_background_is_unverified_and_invalid_input_fails(self):
        result = self.inspect(business_context="听说考试周销量下降")
        self.assertEqual(result["user_context"]["evidence_status"], "user_supplied_unverified")
        self.assertFalse(result["trend_assessed"])
        for bad in [{"target": []}, {"start_date": True}, {"start_date": "2026-10-03", "end_date": "2026-10-01"},
                    {"business_context": {}}, {"business_context": "x" * 801}]:
            with self.subTest(parameters=bad), self.assertRaises(research.AnalysisError):
                self.inspect(**bad)

    def test_tool_registration_projection_audit_and_responses_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            database = snapshot / "snapshot.sqlite"
            with sqlite3.connect(database) as disk:
                self.connection.backup(disk)
            manifest = {**self.manifest, "snapshot_version": "factor_fixture", "tool_version": research.TOOL_VERSION,
                        "database_sha256": research.digest(database), "source_sha256": "PRIVATE_HASH",
                        "source_file": "PRIVATE_FILE.xlsx"}
            (snapshot / "manifest.json").write_text(json.dumps(manifest))
            state = root / "state"
            registry = build_research_tools(snapshot, state)
            schema = next(t for t in registry.definitions() if t["name"] == "inspect_factors")
            self.assertEqual(schema["type"], "function")
            self.assertFalse(schema["parameters"]["additionalProperties"])
            llm = ScriptedLLM(response(call("inspect_factors", {"target": "sales", "start_date": None,
                                                                "end_date": None})), response(message()))
            result = ResearchAgent(llm, registry, state).ask("销量差异有哪些候选factor？")
            self.assertEqual(result["evidence"][0]["result"]["factor_count"], 11)
            self.assertNotIn("PRIVATE_HASH", json.dumps(llm.requests))
            self.assertNotIn("PRIVATE_FILE", json.dumps(llm.requests))
            audit = [json.loads(line) for line in (state / "analysis_audit.jsonl").read_text().splitlines()]
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["tool"], "inspect_factors")
            self.assertEqual(audit[0]["result"]["statistical_tests_performed"], 0)
            for params in [{"target": "all", "start_date": None, "end_date": None, "sql": "SELECT *"},
                           {"target": "all", "start_date": None, "end_date": None, "customer_id": "x"}]:
                self.assertEqual(registry.execute("inspect_factors", json.dumps(params), "test")["status"], "error")
            good = result["evidence"][0]["result"]
            exported = to_markdown(good)
            self.assertIn("factor_fixture", exported)
            self.assertIn("未进行因果发现", exported)
            self.assertNotIn("PRIVATE_SMALL_GROUP", exported)


if __name__ == "__main__":
    unittest.main()
