import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import research_assistant as research
from socialdog_agent import ResearchAgent, build_research_tools
from socialdog_agent.feishu_source import EXPECTED_TABLES, FeishuSnapshotSource, read_json
from socialdog_agent.llm import LLMError
from test_chat_agent import ScriptedLLM, response, call, message


def payload(count=20, cutoff="2026-10-01T23:59:59+08:00"):
    rows = []
    for i in range(count):
        rows.append({"源record_id": f"rec_{i}", "匿名订单编号": f"order_{i}",
                     "匿名顾客编号": f"private_member_{i}", "顾客标识类型": "会员",
                     "下单时间": "2026-10-01T12:00:00+08:00", "数据截至时间": cutoff,
                     "系统已支付": True, "测试核实状态": "待店方确认", "订单应付总额": 12,
                     "已退金额": 2, "系统支付净额": 10, "钱包支付额": 0, "优惠券减免": 0,
                     "配送方式": "PICKUP", "销售来源": "PAD_POS"})
    tables = {name: {"table_id": name, "rows": [], "field_names": []} for name in EXPECTED_TABLES}
    tables["02 订单"] = {"table_id": "orders", "rows": rows, "field_names": sorted(rows[0])}
    tables["03 订单商品明细"] = {"table_id": "items", "rows": [{"源record_id": "detail_1"}],
                              "field_names": ["源record_id"]}
    fingerprint = hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest()
    return {"tables": tables, "summary": {"来源内容SHA256": fingerprint, "来源数据截至": cutoff,
            "各表行数": {name: len(t["rows"]) for name, t in tables.items()}, "订单数": count,
            "支付状态未判定单数": 0, "支付观察单数": count, "支付观察净额_元": 10 * count}}


class FeishuSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sync = self.root / "sync.json"
        self.source = FeishuSnapshotSource(state=self.root / "state", sync_state=self.sync)

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, data, checked):
        folder = self.root / ("batch_" + checked[:10])
        folder.mkdir(exist_ok=True)
        (folder / "clean_result.json").write_text(json.dumps(data))
        self.sync.write_text(json.dumps({"last_successful_output_dir": str(folder),
             "last_successful_fingerprint": data["summary"]["来源内容SHA256"],
             "last_source_cutoff": data["summary"]["来源数据截至"], "last_successful_sync_at": checked}))

    def test_latest_daily_publication_is_picked_up_and_ids_stay_local(self):
        old, _ = self.source.ingest_payload(payload(), checked_at="2026-10-01T23:59:59+08:00")
        self.publish(payload(21, "2026-10-02T23:59:59+08:00"), "2026-10-03T00:01:00+08:00")
        new, info = self.source.resolve()
        self.assertNotEqual(old, new)
        result = build_research_tools(new, self.root / "audit", info).execute(
            "query_aggregate", '{"by":"all","start_date":null,"end_date":null}', "总计")
        self.assertEqual(result["rows"][0]["paid_observed_orders"], 21)
        self.assertNotIn("private_member", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(info))
        manifest, connection = research.read_snapshot(new)
        self.assertNotIn("private_member", str(connection.execute("SELECT * FROM orders").fetchall()))
        connection.close()

    def test_unchanged_content_reuses_snapshot_but_updates_check_time(self):
        first, _ = self.source.ingest_payload(payload(), checked_at="2026-10-02T00:00:00+08:00")
        first_hash = research.digest(first / "snapshot.sqlite")
        second, info = self.source.ingest_payload(payload(), checked_at="2026-10-03T00:00:00+08:00")
        self.assertEqual(first, second)
        self.assertEqual(first_hash, research.digest(second / "snapshot.sqlite"))
        self.assertEqual(info["source_checked_at"], "2026-10-03T00:00:00+08:00")

    def test_failed_new_batch_is_not_silently_replaced_with_old_data(self):
        self.source.ingest_payload(payload(), checked_at="2026-10-02T00:00:00+08:00")
        before = self.source.pointer.read_bytes()
        broken = payload(21, "2026-10-02T23:59:59+08:00")
        broken["summary"]["支付观察净额_元"] = 0
        self.publish(broken, "2026-10-03T00:01:00+08:00")
        with self.assertRaises(research.AnalysisError):
            self.source.resolve()
        self.assertEqual(self.source.pointer.read_bytes(), before)

    def test_cutoff_rollback_is_rejected(self):
        self.source.ingest_payload(payload(), checked_at="2026-10-02T00:00:00+08:00")
        self.publish(payload(21, "2026-09-30T23:59:59+08:00"), "2026-10-03T00:00:00+08:00")
        with self.assertRaises(research.AnalysisError):
            self.source.resolve()

    def test_modified_cache_is_rejected(self):
        self.source.ingest_payload(payload(), checked_at="2026-10-02T00:00:00+08:00")
        path = Path(read_json(self.source.pointer)["payload"])
        path.write_text(path.read_text() + " ")
        with self.assertRaises(research.AnalysisError):
            self.source.resolve()

    def test_same_fingerprint_cannot_hide_changed_rows(self):
        original = payload()
        self.source.ingest_payload(original, checked_at="2026-10-02T00:00:00+08:00")
        before = self.source.pointer.read_bytes()
        altered = copy.deepcopy(original)
        altered["tables"]["02 订单"]["rows"][0]["配送方式"] = "CHANGED"
        with self.assertRaises(research.AnalysisError):
            self.source.ingest_payload(altered, checked_at="2026-10-03T00:00:00+08:00")
        self.assertEqual(self.source.pointer.read_bytes(), before)

    def test_check_time_is_distinct_from_data_time_and_staleness_is_explicit(self):
        candidate = {"checked_at": "2026-10-14T12:00:00+08:00",
                     "data_as_of": "2026-10-10T16:38:13+08:00", "fingerprint": "a" * 64}
        recent = self.source.public_info(candidate, datetime.fromisoformat("2026-10-14T13:00:00+08:00"))
        stale = self.source.public_info(candidate, datetime.fromisoformat("2026-10-16T13:00:00+08:00"))
        self.assertEqual(recent["freshness_status"], "within_daily_check_window")
        self.assertNotEqual(recent["source_checked_at"], recent["source_data_as_of"])
        self.assertEqual(recent["source_data_lag_calendar_days"], 4)
        self.assertEqual(stale["freshness_status"], "check_overdue")

    def test_each_question_pins_a_version_and_next_question_refreshes(self):
        first, _ = self.source.ingest_payload(payload(), checked_at="2026-10-02T00:00:00+08:00")
        source = self.source

        class UpdatingLLM(ScriptedLLM):
            def create_response(self, **request):
                if not self.requests:
                    source.ingest_payload(payload(21, "2026-10-02T23:59:59+08:00"),
                                          checked_at="2026-10-03T00:00:00+08:00")
                return super().create_response(**request)

        args = {"by": "all", "start_date": None, "end_date": None}
        llm = UpdatingLLM(response(call("query_aggregate", args)), response(message()),
                          response(call("query_aggregate", args)), response(message()))

        def prepare():
            path, info = source.resolve()
            return build_research_tools(path, self.root / "audit", info)

        agent = ResearchAgent(llm, state=self.root / "audit", tool_factory=prepare)
        old_answer = agent.ask("总计")
        new_answer = agent.ask("总计")
        self.assertEqual(old_answer["evidence"][0]["result"]["rows"][0]["paid_observed_orders"], 20)
        self.assertEqual(new_answer["evidence"][0]["result"]["rows"][0]["paid_observed_orders"], 21)
        self.assertNotEqual(old_answer["snapshot_versions"], new_answer["snapshot_versions"])

    def test_missing_source_stops_before_network(self):
        llm = ScriptedLLM()

        def prepare():
            path, info = self.source.resolve()
            return build_research_tools(path, self.root / "audit", info)

        with self.assertRaises(LLMError):
            ResearchAgent(llm, state=self.root / "audit", tool_factory=prepare).ask("最新订单")
        self.assertEqual(llm.requests, [])


if __name__ == "__main__":
    unittest.main()
