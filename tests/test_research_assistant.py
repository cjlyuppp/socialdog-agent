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



class TimeSeriesAndProductTest(unittest.TestCase):
    """Day/week/month series, tool-computed exposure, and item-level aggregation."""

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(research.SCHEMA)
        orders, details = [], []
        # 2026-10-01 (Thu) .. 2026-10-14; no orders on Monday 10-05; 25 members per day.
        for day in [d for d in range(1, 15) if d != 5]:
            for i in range(25):
                number = f"o{day}_{i}"
                orders.append(row(f"m{i}", 下单时间=datetime(2026, 10, day, 12), 匿名订单编号=number,
                                  优惠券减免=1 if i < 5 else 0))
                details.append({"匿名订单编号": number, "历史商品名称": "美式" if i % 5 == 0 else "拿铁",
                                "数量": 2, "系统支付净额": 10, "明细应付总额": 12, "明细已退金额": 2})
        details.append({"匿名订单编号": "missing", "历史商品名称": "孤儿明细", "数量": 1})
        records = [research.normalized_order(r, b"k" * 32) for r in orders]
        items, self.report = research.normalized_items(details, orders, records)
        self.connection.executemany(research.INSERT, records)
        self.connection.executemany(research.INSERT_ITEM, items)

    def tearDown(self):
        self.connection.close()

    def test_weekday_exposure_is_computed_not_estimated(self):
        result = research.query_aggregate(self.connection, by="weekday")
        monday = next(r for r in result["rows"] if r["group"] == 1)
        self.assertEqual((monday["days_in_scope"], monday["days_with_orders"]), (2, 1))
        self.assertEqual(monday["orders_per_day_with_orders"], 25.0)
        sunday = next(r for r in result["rows"] if r["group"] == 7)
        self.assertEqual((sunday["days_in_scope"], sunday["days_with_orders"]), (2, 2))

    def test_week_and_month_flag_partial_periods(self):
        weeks = research.query_aggregate(self.connection, by="week")["rows"]
        self.assertEqual([w["group"] for w in weeks], ["2026-09-28", "2026-10-05", "2026-10-12"])
        self.assertEqual([w["period_complete"] for w in weeks], [False, True, False])
        self.assertEqual(weeks[0]["days_in_scope"], 4)
        month = research.query_aggregate(self.connection, by="month")["rows"][0]
        self.assertEqual((month["group"], month["days_in_scope"], month["days_with_orders"]), ("2026-10", 14, 13))
        self.assertFalse(month["period_complete"])

    def test_day_series_counts_suppressed_groups_without_details(self):
        self.connection.execute(research.INSERT, research.normalized_order(
            row("lonely", 下单时间=datetime(2026, 10, 5, 9)), b"k" * 32))
        result = research.query_aggregate(self.connection, by="day")
        self.assertEqual(result["suppressed_group_count"], 1)
        self.assertNotIn("2026-10-05", json.dumps(result))

    def test_coupon_use_is_descriptive_with_caveat(self):
        result = research.query_aggregate(self.connection, by="coupon_use")
        self.assertEqual({r["group"] for r in result["rows"]}, {"未使用优惠券"})
        self.assertEqual(result["suppressed_group_count"], 1)  # Only 5 members ever used a coupon.
        self.assertIn("不是优惠券效果", "".join(result["notes"]))

    def test_product_aggregation_and_monthly_series(self):
        self.assertEqual(self.report["item_lines_unlinked_or_unnamed"], 1)
        self.assertEqual(self.report["matched_item_fields"]["product"], "历史商品名称")
        result = research.query_products(self.connection)
        latte = next(r for r in result["rows"] if r["product"] == "拿铁")
        self.assertEqual(latte["recorded_quantity"], 13 * 20 * 2)
        self.assertEqual(latte["net_per_unit_yuan"], 5.0)
        self.assertEqual(latte["confirmed_members_lower_bound"], 20)
        self.assertNotIn("美式", json.dumps(result, ensure_ascii=False))
        monthly = research.query_products(self.connection, period="month", product="美式")
        self.assertEqual(monthly["rows"], [])  # Only 5 members order 美式: below the threshold.
        self.assertEqual(monthly["suppressed_group_count"], 1)

    def test_product_tool_reports_missing_items(self):
        empty = sqlite3.connect(":memory:")
        empty.executescript(research.SCHEMA)
        self.assertEqual(research.query_products(empty)["status"], "unavailable")
        empty.close()

    def test_as_of_drops_later_orders(self):
        orders = [row(str(i), 下单时间=datetime(2026, 10, 1 + i % 3, 12)) for i in range(9)]
        records = [research.normalized_order(r, b"k" * 32) for r in orders]
        cutoff = datetime(2026, 10, 3, 23, 59, 59, tzinfo=research.TZ)
        kept, kept_records, new_cutoff, info = research.apply_as_of(orders, records, cutoff, "2026-10-02")
        self.assertEqual(len(kept_records), 6)
        self.assertEqual(info["orders_removed_after_as_of"], 3)
        self.assertEqual(new_cutoff.date().isoformat(), "2026-10-02")


if __name__ == "__main__":
    unittest.main()
