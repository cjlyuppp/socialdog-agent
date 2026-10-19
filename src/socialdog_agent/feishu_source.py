"""Resolve the newest verified Feishu daily-sync batch, never a user-uploaded PDF.

The existing Codex heartbeat exports Feishu through its official UI. This module
ingests that export locally, builds a private read-only snapshot, and lets every
new agent question pick up the latest completed batch. It does not pretend that
reading a cache is a live Feishu API request.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

import feishu_daily_clean as cleaning
import research_assistant as research

ROOT = research.ROOT
DEFAULT_SYNC_STATE = ROOT / "feishu_sync_state.json"
EXPECTED_TABLES = {
    "00 研究说明与口径": "来源研究口径", "01 商品规格与价格快照": "商品价格快照",
    "02 订单": "订单数据", "03 订单商品明细": "商品明细",
    "04 匿名员工与计薪规则": "计薪规则", "05 班次考勤与工时": "班次工时",
    "06 绩效工资项目": "绩效项目", "07 成本与研究事件补录": "成本事件",
}


def timestamp(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(research.TZ)
    except (ValueError, TypeError):
        raise research.AnalysisError("飞书来源时间必须是包含时区的ISO日期时间。") from None


def read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise research.AnalysisError("飞书同步状态格式异常。")
    return result


def atomic_json(path: Path, value: dict) -> None:
    research.private_dir(path.parent)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(research.encoded(value))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_payload(payload: dict) -> tuple[list[dict], list[dict], datetime, str]:
    tables, summary = payload["tables"], payload["summary"]
    if set(tables) != set(EXPECTED_TABLES):
        raise research.AnalysisError("飞书来源表结构变化，需核查后再更新分析版本。")
    fingerprint = summary["来源内容SHA256"]
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise research.AnalysisError("飞书来源内容指纹缺失或无效。")
    cutoff = timestamp(summary["来源数据截至"])
    inventory = []
    for source_name, name in EXPECTED_TABLES.items():
        table = tables[source_name]
        if len(table["rows"]) != summary["各表行数"][source_name]:
            raise research.AnalysisError("飞书来源行数与清洗摘要不一致。")
        inventory.append({"name": name, "row_count": len(table["rows"]), "fields": table["field_names"]})
    orders = tables["02 订单"]["rows"]
    if not orders or not tables["03 订单商品明细"]["rows"] or len(orders) != summary["订单数"]:
        raise research.AnalysisError("飞书订单或明细为空/不完整，保留上次成功版本。")
    if summary["支付状态未判定单数"] != 0:
        raise research.AnalysisError("飞书支付状态未知，不能生成完整分析快照。")
    for field in ["源record_id", "匿名订单编号"]:
        ids = [r.get(field) for r in orders]
        if any(i in (None, "") for i in ids) or len(set(ids)) != len(ids):
            raise research.AnalysisError("飞书订单主键缺失或重复，拒绝更新。")
    normalized = []
    for source in orders:
        row = dict(source)
        row["下单时间"] = timestamp(row["下单时间"])
        if timestamp(row["数据截至时间"]) != cutoff or row["下单时间"] > cutoff:
            raise research.AnalysisError("飞书订单来源截至未统一或订单晚于截至时间。")
        if type(row["系统已支付"]) is not bool:
            raise research.AnalysisError("飞书支付状态必须经过确认。")
        row["本次_支付观察"] = int(row["系统已支付"] and row["测试核实状态"] != "明确测试标记")
        normalized.append(row)
    return normalized, inventory, cutoff, fingerprint


class FeishuSnapshotSource:
    def __init__(self, *, state: Path = research.DEFAULT_STATE,
                 sync_state: Path = DEFAULT_SYNC_STATE):
        self.state, self.sync_state = state.resolve(), sync_state.resolve()
        self.pointer = self.state / "feishu_source.json"
        self.snapshots = self.state / "feishu_snapshots"

    @contextmanager
    def lock(self):
        research.private_dir(self.state)
        with (self.state / ".feishu_source.lock").open("a") as stream:
            os.fchmod(stream.fileno(), 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _snapshot(self, payload: dict) -> Path:
        orders, inventory, cutoff, fingerprint = validate_payload(payload)
        # The tool version is part of the path: a schema upgrade rebuilds instead of failing on old files.
        path = self.snapshots / f"{fingerprint}_v{research.TOOL_VERSION}"
        if path.exists() and not (self.state / "customer_hmac.key").exists():
            raise research.AnalysisError("本地匿名化密钥缺失，不能验证飞书批次的一致性。")
        key = research.load_key(self.state)
        records = [research.normalized_order(row, key) for row in orders]
        items, item_report = research.normalized_items(payload["tables"]["03 订单商品明细"]["rows"], orders, records)
        paid = [r for r in records if r[6] and not r[7]]
        if (len(paid) != payload["summary"]["支付观察单数"] or
                sum(r[9] for r in paid) != research.cents(payload["summary"]["支付观察净额_元"])):
            raise research.AnalysisError("飞书清洗汇总与独立重算的支付单量或净额不一致。")
        if path.exists():
            manifest, connection = research.read_snapshot(path)
            try:
                previous_records = connection.execute("SELECT * FROM orders").fetchall()
                previous_items = connection.execute("SELECT * FROM items").fetchall()
            finally:
                connection.close()
            canonical_rows = lambda rows: sorted(research.encoded(list(row)) for row in rows)
            if (manifest["source_sha256"] != fingerprint or manifest["source_tables"] != inventory or
                    canonical_rows(previous_records) != canonical_rows(records) or
                    canonical_rows(previous_items) != canonical_rows(items)):
                raise research.AnalysisError("同一飞书来源指纹对应不同内容，拒绝用旧快照替代。")
            return path
        research.private_dir(self.snapshots)
        staged = self.snapshots / (".pending_" + uuid.uuid4().hex)
        research.write_analysis_snapshot(records, inventory, cutoff, fingerprint, "Feishu daily export", staged,
            sum(r["匿名顾客编号"] in (None, "") for r in orders),
            {"kind": "feishu_daily_sync", "hash_kind": "full_source_content_fingerprint"}, items, item_report)
        os.replace(staged, path)
        return path

    def ingest_base(self, path: Path) -> tuple[Path, dict]:
        # Only a native .base export obtained through the official UI is accepted here.
        payload = cleaning.clean(path)
        return self.ingest_payload(payload, checked_at=datetime.now(research.TZ).isoformat())

    def ingest_payload(self, payload: dict, *, checked_at: str) -> tuple[Path, dict]:
        checked = timestamp(checked_at)
        with self.lock():
            snapshot = self._snapshot(payload)  # Fail before changing any active pointer.
            batch = self.state / "feishu_batches" / uuid.uuid4().hex
            research.private_dir(batch)
            payload_path = batch / "clean_result.json"
            atomic_json(payload_path, payload)
            previous = read_json(self.pointer) if self.pointer.exists() else {}
            summary = payload["summary"]
            candidate = {"payload": str(payload_path), "payload_sha256": research.digest(payload_path),
                         "fingerprint": summary["来源内容SHA256"], "checked_at": checked.isoformat(),
                         "data_as_of": summary["来源数据截至"], "snapshot": str(snapshot),
                         "origin": "official_feishu_export"}
            self._reject_rollback(previous, candidate)
            atomic_json(self.pointer, candidate)
        return snapshot, self.public_info(candidate)

    @staticmethod
    def _reject_rollback(previous: dict, candidate: dict) -> None:
        if previous and timestamp(candidate["data_as_of"]) < timestamp(previous["data_as_of"]):
            raise research.AnalysisError("飞书来源截至时间倒退，保留上次成功版本，需人工核查。")

    def resolve(self) -> tuple[Path, dict]:
        with self.lock():
            previous = read_json(self.pointer) if self.pointer.exists() else {}
            candidates = [previous] if previous else []
            if self.sync_state.exists():
                sync = read_json(self.sync_state)
                folder = sync.get("last_successful_output_dir")
                checked = sync.get("last_checked_at") or sync.get("last_successful_sync_at")
                if folder and checked:
                    payload_path = Path(folder) / "clean_result.json"
                    candidates.append({"payload": str(payload_path), "checked_at": checked,
                        "fingerprint": sync["last_successful_fingerprint"],
                        "data_as_of": sync["last_source_cutoff"], "origin": "daily_sync_publication"})
            if not candidates:
                raise research.AnalysisError("尚无成功的飞书同步版本，请先运行每日同步或导入官方.base快照。")
            chosen = max(candidates, key=lambda c: timestamp(c["checked_at"]))
            self._reject_rollback(previous, chosen)
            payload_path = Path(chosen["payload"])
            payload_hash = research.digest(payload_path)
            if chosen.get("payload_sha256") and chosen["payload_sha256"] != payload_hash:
                raise research.AnalysisError("飞书本地同步文件发生变化，拒绝分析。")
            payload = read_json(payload_path)
            if (payload["summary"]["来源内容SHA256"] != chosen["fingerprint"] or
                    timestamp(payload["summary"]["来源数据截至"]) != timestamp(chosen["data_as_of"])):
                raise research.AnalysisError("飞书同步状态与数据内容版本不一致，拒绝分析。")
            snapshot = self._snapshot(payload)
            chosen = {**chosen, "payload_sha256": payload_hash, "snapshot": str(snapshot)}
            atomic_json(self.pointer, chosen)
            return snapshot, self.public_info(chosen)

    @staticmethod
    def public_info(candidate: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(research.TZ)
        checked, cutoff = timestamp(candidate["checked_at"]), timestamp(candidate["data_as_of"])
        age = now - checked
        recent = timedelta(0) <= age <= timedelta(hours=26)
        return {"kind": "feishu_daily_sync", "name": "金典飞书多维表格 · 每日核验版本",
                "retrieval_mode": "latest_verified_daily_sync_not_live_api",
                "source_checked_at": checked.isoformat(), "source_data_as_of": cutoff.isoformat(),
                "source_data_lag_calendar_days": (now.date() - cutoff.date()).days,
                "freshness_status": "within_daily_check_window" if recent else "check_overdue",
                "source_content_version": candidate["fingerprint"][:12],
                "notice": ("来源通过官方飞书导出和每日同步读取；每次提问重新选取最新已核验版本。"
                           "来源核验时间不等于数据截至时间，不能把历史数据描述成今天的经营数据。" +
                           (" 最近核验已超过每日检查窗口，未确认飞书当前是否存在新变化。" if not recent else ""))}


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书每日同步与研究Agent的本地连接入口")
    parser.add_argument("--import-base", type=Path, help="由官方飞书UI下载的完整.base文件")
    parser.add_argument("--state-dir", type=Path, default=research.DEFAULT_STATE)
    parser.add_argument("--sync-state", type=Path, default=DEFAULT_SYNC_STATE)
    args = parser.parse_args()
    try:
        source = FeishuSnapshotSource(state=args.state_dir, sync_state=args.sync_state)
        snapshot, info = source.ingest_base(args.import_base) if args.import_base else source.resolve()
        manifest, connection = research.read_snapshot(snapshot)
        connection.close()
        print(research.encoded({"status": "ok", "snapshot_version": manifest["snapshot_version"],
                               "orders_in_source": manifest["orders_in_source"], "data_source": info}))
        return 0
    except (research.AnalysisError, OSError, ValueError, KeyError, TypeError):
        print(research.encoded({"status": "error", "error": "飞书同步版本校验失败；旧版本保留，需检查来源和同步日志。"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
