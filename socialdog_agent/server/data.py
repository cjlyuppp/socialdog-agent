"""Separate agent packages and full reference data at the connection boundary."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import historical_backtest as history
from socialdog_agent.history_tools import build_history_tools, to_yuan

from .models import EvalQuestion, ReferenceQuery
from .store import encoded

GROUP_LABELS = dict(zip(history.GROUPS, [
    "总计", "按日", "按周", "按月", "按小时", "按星期", "按分类", "按商品", "按配送方式", "按收款方式", "按订单状态", "按交易类型",
]))


@contextmanager
def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def build_reference(package: Path, output: Path, cutoff: str):
    """Copy a verified full package into a standalone read-only reference database."""
    cutoff = date.fromisoformat(cutoff).isoformat()
    manifest = history.read_package(package)
    description = manifest["data_description"]
    if description.get("as_of") is not None:
        raise ValueError("数据核对库必须从未截断的完整回测包构建")
    output = Path(output).resolve()
    if output.name != "reference.sqlite" or output.exists():
        raise ValueError("请选择尚不存在的 reference.sqlite 输出文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".reference-", suffix=".sqlite", dir=output.parent)
    os.close(fd)
    try:
        with readonly(Path(package) / "history.sqlite") as source:
            destination = sqlite3.connect(temporary)
            try:
                source.backup(destination)
                destination.execute("CREATE TABLE reference_meta (value TEXT NOT NULL)")
                destination.execute("INSERT INTO reference_meta VALUES (?)", (encoded({
                    "dataset_version": description["dataset_version"], "cutoff_date": cutoff,
                    "observation_start": description["observation_start"][:10],
                    "observation_end": description["observation_end"][:10],
                }),))
                destination.commit()
            finally:
                destination.close()
        # An existing reference is never overwritten, including a concurrent build.
        os.link(temporary, output)
        output.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


class AgentData:
    """The worker only receives this object; it has no reference database path."""
    def __init__(self, package: Path):
        self.package = Path(package).resolve()
        self.description = history.read_package(self.package)["data_description"]
        self.cutoff = self.description.get("as_of")
        if not self.cutoff:
            raise ValueError("多人服务必须使用带 --as-of 的物理截断回测包")
        date.fromisoformat(self.cutoff)
        with readonly(self.package / "history.sqlite") as db:
            if db.execute("SELECT count(*) FROM lines WHERE order_date>? OR substr(payment_at,1,10)>?", (self.cutoff, self.cutoff)).fetchone()[0]:
                raise ValueError("agent 数据包包含截止日期之后的记录")

    def tools(self, state, config):
        registry = build_history_tools(self.package, state, config["min_orders_per_group"])
        if registry.source_info["dataset_version"] != config["dataset_version"]:
            raise ValueError("任务受理后数据版本发生变化，请重启服务")
        return registry

    def status(self, settings, model):
        return {"dataset_version": self.description["dataset_version"], "cutoff_date": self.cutoff,
                "agent_observation_start": self.description["observation_start"][:10],
                "agent_observation_end": self.description["observation_end"][:10],
                "min_orders_per_group": settings["min_orders_per_group"], "model": model}


class ReferenceData:
    def __init__(self, path: Path, cutoff: str):
        self.path = Path(path).resolve()
        if self.path.name != "reference.sqlite":
            raise ValueError("数据核对接口只接受独立 reference.sqlite")
        with readonly(self.path) as db:
            self.meta = json.loads(db.execute("SELECT value FROM reference_meta").fetchone()[0])
        if self.meta["cutoff_date"] != cutoff:
            raise ValueError("数据核对库与 agent 的截止日期不一致")
        self.cutoff = cutoff

    def metadata(self, *, can_view_after_cutoff=False):
        where, parameters = ("", ()) if can_view_after_cutoff else (" WHERE order_date<=?", (self.cutoff,))
        with readonly(self.path) as db:
            start, end = db.execute("SELECT min(order_date),max(order_date) FROM lines" + where, parameters).fetchone()
            products = [row[0] for row in db.execute("SELECT DISTINCT product FROM lines" + where +
                        (" AND" if where else " WHERE") + " product IS NOT NULL ORDER BY product", parameters)]
        return {**self.meta, "observation_start": start or self.cutoff, "observation_end": end or self.cutoff,
                "groups": [{"value": key, "label": value} for key, value in GROUP_LABELS.items()],
                "products": products, "can_view_after_cutoff": can_view_after_cutoff}

    def query(self, query: ReferenceQuery, *, can_view_after_cutoff=False):
        args = query.model_dump(mode="json")
        after = (date.fromisoformat(self.cutoff) + timedelta(days=1)).isoformat()
        rows = []
        with readonly(self.path) as db:
            # Split mixed groups as well: a product/weekday can have data on both sides of T.
            for is_after in ((False, True) if can_view_after_cutoff else (False,)):
                selected = dict(args)
                if is_after:
                    selected["start_date"] = max(args["start_date"] or after, after)
                else:
                    selected["end_date"] = min(args["end_date"] or self.cutoff, self.cutoff)
                if selected["start_date"] and selected["end_date"] and selected["start_date"] > selected["end_date"]:
                    continue
                result = to_yuan(history.aggregate_connection(db, selected))
                rows.extend({**row, "after_cutoff": is_after} for row in result["rows"])
        rows.sort(key=lambda r: (int(r["group"]) if args["by"] in ("hour", "weekday") else r["group"], r["after_cutoff"]))
        return {"query": args, "rows": rows, "group_order_counts_additive": args["by"] not in ("category", "product", "type"),
                "notes": ["金额为来源记录收款，不等于净收入、利润或银行到账。",
                          "同一分组按截止日期前后分别列示。" if can_view_after_cutoff else f"仅返回 {self.cutoff} 及之前的数据。"]}


def load_questions(path: Path | None):
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("题库必须为 JSON 数组")
    questions = {}
    for entry in payload:
        question = EvalQuestion.model_validate(entry)
        if question.id in questions or not question.question.strip():
            raise ValueError("题库编号重复或问题为空")
        if question.reference_hint:
            ReferenceQuery.model_validate(question.reference_hint.model_dump(mode="json", exclude_unset=True))
        public = question.model_dump(mode="json")
        if question.reference_hint:
            public["reference_hint"] = question.reference_hint.model_dump(mode="json", exclude_unset=True)
        questions[question.id] = public
    return questions
