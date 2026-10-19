from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import research_assistant as research


def row(identifier="member", **changes):
    record = {"系统已支付": True, "下单时间": datetime(2026, 10, 1, 12),
              "测试核实状态": "待店方确认", "本次_支付观察": 1,
              "订单应付总额": 12, "已退金额": 2, "系统支付净额": 10,
              "匿名顾客编号": identifier, "顾客标识类型": "会员",
              "配送方式": "PICKUP", "销售来源": "PAD_POS",
              "钱包支付额": 0, "优惠券减免": 0}
    record.update(changes)
    return record


class ResearchToolsTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(research.SCHEMA)

    def tearDown(self):
        self.connection.close()

    def add(self, rows):
        self.connection.executemany(research.INSERT, [research.normalized_order(r, b"k" * 32) for r in rows])

    def test_19_people_remain_suppressed_despite_100_orders(self):
        self.add([row(str(i % 19)) for i in range(100)])
        result = research.query_aggregate(self.connection)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["status"], "unavailable")

    def test_exactly_20_members_and_refunds(self):
        self.add([row(str(i)) for i in range(20)])
        result = research.query_aggregate(self.connection)["rows"][0]
        self.assertEqual(result["confirmed_members_lower_bound"], 20)
        self.assertEqual(result["paid_observed_orders"], 20)
        self.assertEqual(result["system_net_yuan"], 200)
        self.assertEqual(result["cumulative_refund_yuan"], 40)

    def test_guest_and_missing_ids_do_not_satisfy_threshold(self):
        self.add([row(str(i), 顾客标识类型="游客") for i in range(30)])
        self.add([row(None, 顾客标识类型="缺少标识") for _ in range(50)])
        self.assertEqual(research.query_aggregate(self.connection)["rows"], [])

    def test_unknown_ids_contribute_orders_after_threshold_met(self):
        self.add([row(str(i)) for i in range(20)] + [row(None, 顾客标识类型="缺少标识")])
        self.assertEqual(research.query_aggregate(self.connection)["rows"][0]["paid_observed_orders"], 21)

    def test_small_groups_do_not_leak_labels_or_metrics(self):
        self.add([row(str(i)) for i in range(20)] + [row("small", 配送方式="DELIVERY")])
        result = research.query_aggregate(self.connection, by="delivery")
        self.assertEqual(len(result["rows"]), 1)
        self.assertNotIn("DELIVERY", json.dumps(result))

    def test_unpaid_tests_and_full_refunds_follow_defined_population(self):
        self.add([row(str(i)) for i in range(20)])
        self.add([row("unpaid", 系统已支付=False, 本次_支付观察=0, 系统支付净额=0),
                  row("test", 测试核实状态="明确测试标记", 本次_支付观察=0),
                  row("refunded", 已退金额=12, 系统支付净额=0)])
        result = research.query_aggregate(self.connection)["rows"][0]
        self.assertEqual(result["paid_observed_orders"], 21)
        self.assertEqual(result["system_net_yuan"], 200)

    def test_zero_missing_precision_and_status(self):
        self.assertEqual(research.cents(0), 0)
        self.assertEqual(research.cents(0.1 + 0.2), 30)
        for bad in [None, True, float("nan"), 0.001]:
            with self.assertRaises(research.AnalysisError):
                research.cents(bad)
        with self.assertRaises(research.AnalysisError):
            research.normalized_order(row(系统已支付=None), b"k" * 32)

    def test_sql_and_invalid_date_rejected(self):
        for parameters in [{"by": "hour; DROP TABLE orders"}, {"by": []}, {"start_date": "20261001"},
                           {"start_date": "2026-10-03", "end_date": "2026-10-02"}]:
            with self.assertRaises(research.AnalysisError):
                research.query_aggregate(self.connection, **parameters)

    def test_date_bounds_are_inclusive(self):
        self.add([row(str(i)) for i in range(20)])
        result = research.query_aggregate(self.connection, start_date="2026-10-01", end_date="2026-10-01")
        self.assertEqual(result["rows"][0]["paid_observed_orders"], 20)
        self.assertEqual(research.query_aggregate(self.connection, start_date="2026-10-02")["rows"], [])

    def test_anonymous_keys_and_schema_do_not_retain_identifiers(self):
        record = research.normalized_order(row("original-sensitive-id"), b"k" * 32)
        self.assertNotIn("original-sensitive-id", str(record))
        self.assertEqual(record[8], research.normalized_order(row("original-sensitive-id"), b"k" * 32)[8])
        self.assertNotEqual(record[8], research.normalized_order(row("original-sensitive-id"), b"z" * 32)[8])

    def test_snapshot_readonly_tampering_and_all_attempts_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            database = snapshot / "snapshot.sqlite"
            disk = sqlite3.connect(database)
            disk.executescript(research.SCHEMA)
            disk.close()
            manifest = {"snapshot_version": "test", "tool_version": research.TOOL_VERSION,
                        "database_sha256": research.digest(database), "source_sha256": "test"}
            (snapshot / "manifest.json").write_text(json.dumps(manifest))
            _, readonly = research.read_snapshot(snapshot)
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute("DELETE FROM orders")
            readonly.close()
            state = root / "state"
            success = research.run_tool(snapshot, "describe_data", state=state)
            invalid = research.run_tool(snapshot, "query_aggregate", {"customer_id": "a"}, state=state)
            unavailable = research.run_tool(snapshot, "stored_value_analysis", state=state)
            self.assertEqual([success["status"], invalid["status"], unavailable["status"]], ["ok", "error", "unavailable"])
            with database.open("ab") as stream:
                stream.write(b"tampered")
            failure = research.run_tool(snapshot, "describe_data", state=state)
            self.assertEqual(failure["status"], "error")
            audits = [json.loads(line) for line in (state / "analysis_audit.jsonl").read_text().splitlines()]
            self.assertEqual(len(audits), 4)
            self.assertTrue(all(a["statistical_tests_performed"] == 0 for a in audits))


if __name__ == "__main__":
    unittest.main()
