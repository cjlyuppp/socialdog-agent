"""Historical 27-column package exposed to the agent as read-only tools.

The package has no customer IDs, so the live 20-member rule can never be met.
For the trusted research group this mode uses an order-count threshold instead;
the threshold is part of every result so evaluators can see what was hidden.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import historical_backtest as history
import research_assistant as research
from .tools import DATE_PARAMETER, Tool, ToolError, ToolRegistry, allowed_fields, validate_dates

DEFAULT_MIN_ORDERS = 5
QUALITY_SECTIONS = ["payment_time", "duplicates", "refunds", "recharge", "missing", "amount_example",
                    "flag_counts", "baseline_exclusion_reasons"]

HISTORY_INSTRUCTIONS = """数据集：历史27列交易表的离线回测包（不是飞书实时数据）。
- 披露规则：每组至少{min_orders}个订单；未返回的组不代表零，可引用suppressed_group_count。
- 口径：记录收款金额（元）不含余额扣除、优惠券和积分抵扣；这些扣除项不是从记录收款金额中拆出的组成部分。记录收款不是净收入、利润或银行到账；退款金额与件数未知。
- scope=baseline为固定筛选的基准样本，scope=all为全部来源记录；用户没说时用baseline并说明。
- 商品/分类分组的订单数会重叠，不能相加为全店订单数。收款方式不是获客渠道。
- 调价问题用history_price_events：事件由项目单价重建，前后对比是描述性的，不是价格弹性；没有成本，不能回答利润。
- 数据质量问题用history_quality；无法开展的分析用history_unavailable。"""


def to_yuan(value):
    """Recursively rename *_cents to *_yuan so the model never converts units itself."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.endswith("_cents") and (item is None or isinstance(item, (int, float))) \
                    and not isinstance(item, bool):
                result[key[:-6] + "_yuan"] = None if item is None else round(item / 100, 2)
            else:
                result[key] = to_yuan(item)
        return result
    if isinstance(value, list):
        return [to_yuan(item) for item in value]
    return value


def connect(folder: Path) -> sqlite3.Connection:
    connection = sqlite3.connect((folder / "history.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def aggregate(folder: Path, args: dict, min_orders: int) -> dict:
    result = history.aggregate_sql(folder, args)
    with connect(folder) as connection:
        scope = research.scope_dates(connection, args.get("start_date"), args.get("end_date"), "lines")
        where, parameters = (["baseline_eligible=1"] if args["scope"] == "baseline" else []), []
        for field, operator in [("start_date", ">="), ("end_date", "<=")]:
            if args.get(field):
                where.append(f"order_date {operator} ?")
                parameters.append(args[field])
        order_days = {d for (d,) in connection.execute(
            "SELECT DISTINCT order_date FROM lines" + (" WHERE " + " AND ".join(where) if where else ""), parameters)}
    rows, suppressed = [], 0
    for row in result["rows"]:
        if row["order_count"] < min_orders:
            suppressed += 1
            continue
        days = research.exposure(args["by"], row["group"], scope, order_days)
        rows.append({**row, **days,
                     "orders_per_day_with_orders": research.per_day(row["order_count"], days["days_with_orders"]),
                     "recorded_receipt_per_day_with_orders_cents":
                         None if not days["days_with_orders"] else round(row["recorded_receipt_cents"] / days["days_with_orders"])})
    notes = []
    if args["by"] in {"week", "month"}:
        notes.append("period_complete为false的周期只覆盖部分日历天，不能与完整周期直接比较总量。")
    if args["by"] == "weekday":
        notes.append("各星期的days_in_scope与days_with_orders已由工具计算；日均请直接引用per_day字段。")
    return {**result, "status": "ok" if rows else "unavailable", "rows": rows,
            "privacy": {"minimum_orders_per_group": min_orders,
                        "notice": "仅返回订单数达到门槛的分组；被隐藏分组不返回名称或数字。"},
            "suppressed_group_count": suppressed, "exposure_definition": research.EXPOSURE_DEFINITION,
            "notes": notes, "units": "金额字段单位为元；recorded_quantity为来源记录件数，不是退款后净销量",
            "reason": result["reason"] if not result["rows"] else
                      None if rows else f"所有分组订单数都少于{min_orders}，按披露规则不返回。"}


def price_events(folder: Path, args: dict, min_orders: int) -> dict:
    return suppress_price_windows(history.price_events(history.load_lines(folder), **args), min_orders)


def suppress_price_windows(result: dict, min_orders: int) -> dict:
    """Hide before/after windows with too few product orders; the event itself stays visible."""
    result = json.loads(json.dumps(result))
    for event in result["events"]:
        hidden = [label for label in ["before", "after"] if event[label]["product_order_count"] < min_orders]
        for label in hidden:
            window = event[label]
            event[label] = {"start": window["start"], "end": window["end"], "suppressed": True,
                            "reason": f"窗口内含该商品的订单少于{min_orders}个"}
        if hidden:
            event["change_pct"] = {key: None for key in event["change_pct"]}
    result["privacy"] = {"minimum_orders_per_window": min_orders}
    return result


def build_history_tools(package: Path, state: Path = research.DEFAULT_STATE,
                        min_orders: int = DEFAULT_MIN_ORDERS) -> ToolRegistry:
    package = Path(package).resolve()
    manifest = history.read_package(package)  # Integrity check before any tool is offered.
    description = manifest["data_description"]
    version = description["dataset_version"]
    if isinstance(min_orders, bool) or not isinstance(min_orders, int) or min_orders < 1:
        raise ValueError("历史数据模式的订单门槛必须为正整数。")
    registry = ToolRegistry()
    registry.instructions = HISTORY_INSTRUCTIONS.format(min_orders=min_orders)
    end = description["observation_end"].replace(" ", "T")
    registry.source_info = {
        "kind": "historical_backtest", "name": "历史交易表 · 离线回测包",
        "retrieval_mode": "offline_history_package", "dataset_version": version,
        "source_data_as_of": datetime.fromisoformat(end).replace(tzinfo=research.TZ).isoformat(),
        "as_of": description.get("as_of"), "minimum_orders_per_group": min_orders,
        "notice": "历史数据模式：来源为离线历史交易表，不是今天的经营数据。" +
                  (f" 回测包已在{description['as_of']}截断，之后的记录已物理删除。" if description.get("as_of") else "")}

    def run(name, handler, parameters, question):
        started = datetime.now(research.TZ).isoformat()
        try:
            result = to_yuan(handler(parameters))
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            result = {"status": "error", "error": "历史查询未完成：参数或回测包异常。", "local_error": str(error)}
        result.update(snapshot_version=version, tool_version=history.VERSION, statistical_tests_performed=0)
        research.record_audit(state, {"started_at": started, "finished_at": datetime.now(research.TZ).isoformat(),
                                      "question": question, "tool": name, "parameters": parameters,
                                      "snapshot_version": version, "result": result,
                                      "result_sha256": hashlib.sha256(research.encoded(result).encode()).hexdigest(),
                                      "statistical_tests_performed": 0})
        result.pop("local_error", None)  # Local paths stay in the audit log only.
        return result

    def describe(_):
        # Truncation counts describe hidden future data. They belong in the
        # offline manifest, not in model context or participant evidence.
        public = {key: value for key, value in description.items() if key != "source_rows_removed_after_as_of"}
        return {**public, "privacy": {"minimum_orders_per_group": min_orders},
                "public_query_groups": list(history.GROUPS),
                "metric_mapping": "与网页飞书口径的字段对照见仓库 docs/口径对照.md；两套口径不能直接对比金额。"}

    registry.register(Tool("history_describe", "列出历史回测包的覆盖期间、明细与订单数、基准样本定义、as_of截断和披露规则。",
                           {"type": "object", "properties": {}, "additionalProperties": False},
                           lambda p: allowed_fields(p, set()),
                           lambda p, q: run("history_describe", describe, p, q)))

    def validate_aggregate(p):
        allowed_fields(p, {"scope", "by", "start_date", "end_date", "product"},
                       {"scope", "by", "start_date", "end_date"})
        try:
            history.validate_args(p)
        except ValueError as error:
            raise ToolError(str(error)) from None
        validate_dates(p)

    registry.register(Tool("history_aggregate",
                           "历史交易聚合。scope=baseline基准样本或all全部记录；by可选all/day/week/month/hour/weekday/"
                           "category/product/delivery/payment/status/type；product可筛选单个商品原名。返回订单数、明细数、记录件数、"
                           "记录成交与收款金额（元）、实际天数和按有订单日计算的日均。每组至少达到订单门槛才返回。",
                           {"type": "object", "properties": {
                               "scope": {"type": "string", "enum": ["baseline", "all"]},
                               "by": {"type": "string", "enum": list(history.GROUPS)},
                               "start_date": DATE_PARAMETER, "end_date": DATE_PARAMETER,
                               "product": {"type": ["string", "null"], "description": "商品原名；不限为null"}},
                            "required": ["scope", "by", "start_date", "end_date"], "additionalProperties": False},
                           validate_aggregate,
                           lambda p, q: run("history_aggregate", lambda a: aggregate(package, a, min_orders), p, q)))

    def validate_events(p):
        allowed_fields(p, {"product", "min_run_days", "window_days", "scope"}, {"product"})
        if p["product"] is not None and (not isinstance(p["product"], str) or not 0 < len(p["product"]) <= 100):
            raise ToolError("product必须为1–100字的商品原名或null。")
        for name, low, high in [("min_run_days", 1, 30), ("window_days", 1, 60)]:
            value = p.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high):
                raise ToolError(f"{name}必须为{low}–{high}的整数。")
        if p.get("scope", "baseline") not in {"baseline", "all"}:
            raise ToolError("scope只能为baseline或all。")

    registry.register(Tool("history_price_events",
                           "从项目单价重建历史调价事件（按商品+规格，持续至少min_run_days个销售日的价格变化），"
                           "并给出事件前后各window_days天的商品件数、收款、日均、占全店收款比例及变化百分比、同期其他调价。"
                           "描述性前后对比，不是价格弹性或因果效应；没有成本数据，利润不可得。",
                           {"type": "object", "properties": {
                               "product": {"type": ["string", "null"], "description": "商品原名；全部为null"},
                               "min_run_days": {"type": "integer", "minimum": 1, "maximum": 30, "description": "默认3"},
                               "window_days": {"type": "integer", "minimum": 1, "maximum": 60, "description": "默认14"},
                               "scope": {"type": "string", "enum": ["baseline", "all"], "description": "窗口指标样本，默认baseline"}},
                            "required": ["product"], "additionalProperties": False},
                           validate_events,
                           lambda p, q: run("history_price_events", lambda a: price_events(package, a, min_orders), p, q)))

    def validate_quality(p):
        allowed_fields(p, {"section"}, {"section"})
        if p["section"] not in QUALITY_SECTIONS:
            raise ToolError("section必须为工具定义中列出的数据质量项目。")

    registry.register(Tool("history_quality",
                           "历史数据质量核查：收款时间异常、疑似重复、退款标记、充值、缺失值、金额列示例、标记计数与基准排除原因。",
                           {"type": "object", "properties": {"section": {"type": "string", "enum": QUALITY_SECTIONS}},
                            "required": ["section"], "additionalProperties": False},
                           validate_quality,
                           lambda p, q: run("history_quality", lambda a: {"status": "ok", "section": a["section"],
                                            **history.dispatch(package, "quality", a)}, p, q)))

    limitations = json.loads((package / "limitations.json").read_text(encoding="utf-8"))

    def validate_unavailable(p):
        allowed_fields(p, {"analysis"}, {"analysis"})
        if p["analysis"] not in limitations:
            raise ToolError("analysis必须为工具定义中列出的分析。")

    registry.register(Tool("history_unavailable",
                           "说明历史数据不能开展的分析（净收入、净销量、留存、储值、A/B、等待时间、今天、利润、渠道等）缺什么数据。",
                           {"type": "object", "properties": {"analysis": {"type": "string", "enum": list(limitations)}},
                            "required": ["analysis"], "additionalProperties": False},
                           validate_unavailable,
                           lambda p, q: run("history_unavailable", lambda a: dict(limitations[a["analysis"]]), p, q)))
    return registry
