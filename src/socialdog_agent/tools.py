"""Responses function definitions, argument validation and local dispatch."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Callable

import research_assistant as research


class ToolError(ValueError):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    validate: Callable[[dict], None]
    handler: Callable[[dict, str], dict]

    def definition(self) -> dict:
        # Responses uses a flat tool definition (no nested "function" object).
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": self.parameters}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.source_info: dict = {}
        # Dataset-specific guidance appended to the agent's core instructions.
        self.instructions: str = ""

    def register(self, tool: Tool) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tool.name) or tool.name in self._tools:
            raise ToolError("工具名无效或重复注册。")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict]:
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, name: str, arguments: str, question: str) -> dict:
        try:
            if name not in self._tools:
                raise ToolError("该工具未注册，不能执行。")
            if not isinstance(arguments, str) or len(arguments) > 4096:
                raise ToolError("工具参数必须为长度不超过4096的JSON字符串。")
            try:
                parameters = json.loads(arguments)
            except (ValueError, RecursionError):
                raise ToolError("工具参数不是有效JSON对象。") from None
            if not isinstance(parameters, dict):
                raise ToolError("工具参数必须为JSON对象。")
            tool = self._tools[name]
            tool.validate(parameters)
        except ToolError as error:
            return {"status": "error", "error": str(error)}
        # Handler/audit I/O failures intentionally propagate: no unaudited result.
        return tool.handler(parameters, question)


def validate_dates(p: dict) -> None:
    try:
        for field in ["start_date", "end_date"]:
            research.valid_date(p[field])
    except research.AnalysisError as error:
        raise ToolError(str(error)) from None
    if p["start_date"] and p["end_date"] and p["start_date"] > p["end_date"]:
        raise ToolError("开始日期不能晚于结束日期。")


DATE_PARAMETER = {"type": ["string", "null"], "description": "YYYY-MM-DD；不限为null"}


def allowed_fields(parameters: dict, allowed: set, required: set = frozenset()) -> None:
    if set(parameters) - allowed or required - set(parameters):
        raise ToolError("工具参数存在未允许字段或缺少必填字段；请按工具定义重试。")


PUBLIC_MANIFEST_FIELDS = {"snapshot_version", "tool_version", "timezone", "snapshot_as_of",
                          "observation_start", "last_order_at", "as_of_day_is_partial",
                          "orders_in_source", "source_tables", "identity_coverage", "item_coverage",
                          "as_of_truncation", "public_query_fields", "privacy_min_confirmed_members",
                          "tools", "limitations"}


def public_result(result: dict) -> dict:
    if result.get("status") == "error":
        # Local exceptions may contain source paths; keep their details in the local audit only.
        return {"status": "error", "error": "本地分析未完成：快照或参数异常，请在本地核查日志。",
                **{k: result[k] for k in ["snapshot_version", "tool_version", "statistical_tests_performed"]
                   if k in result}}
    if "source_tables" in result:
        projected = {**{k: v for k, v in result.items() if k in PUBLIC_MANIFEST_FIELDS or k == "status"},
                     "source_table_count": len(result["source_tables"]),
                     "observation_calendar_days": research.observation_calendar_days(result)}
        if "identity_coverage" in projected:
            coverage = dict(projected["identity_coverage"])
            if "missing_customer_id_orders" in coverage:
                coverage["missing_customer_id_orders_in_all_source_orders"] = coverage.pop("missing_customer_id_orders")
            coverage["population"] = "全部来源订单（含未支付与测试），不代表支付观察或任何筛选组"
            projected["identity_coverage"] = coverage
        return projected
    return {k: v for k, v in result.items() if k in {
        "status", "snapshot_version", "tool_version", "statistical_tests_performed",
        "rows", "privacy", "reason", "limitations", "error", "missing", "prerequisites",
        "purpose", "tool", "factor_tool_version", "target", "target_definition", "factor_count", "factors",
        "readiness_counts", "analysis_status", "candidate_origin", "candidate_order", "query_parameters", "data_overview",
        "user_context", "trend_assessed", "suppressed_group_count", "exposure_definition", "notes",
        "released_group_count", "truncated"}}


def build_research_tools(snapshot: Path, state: Path = research.DEFAULT_STATE,
                         source_info: dict | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.source_info = dict(source_info or {})

    def call(name, parameters, question):
        result = public_result(research.run_tool(snapshot, name, parameters,
                                                question=question, state=state))
        if registry.source_info:
            result["data_source"] = registry.source_info
        return result

    def validate_describe(p):
        allowed_fields(p, set())

    registry.register(Tool("describe_data", "列出本地数据表、字段、时间范围、快照版本、数据限制及工具可用性。",
                           {"type": "object", "properties": {}, "additionalProperties": False},
                           validate_describe, lambda p, q: call("describe_data", p, q)))

    def validate_aggregate(p):
        allowed_fields(p, {"by", "start_date", "end_date"}, {"by", "start_date", "end_date"})
        if not isinstance(p["by"], str) or p["by"] not in research.GROUPS:
            raise ToolError("by只能是" + "/".join(research.GROUPS) + "。")
        validate_dates(p)

    registry.register(Tool("query_aggregate",
                           "只读订单聚合。by可选总计all，时间序列day/week/month，下单小时hour、星期weekday、"
                           "配送方式delivery、销售来源sales_source、是否用券coupon_use、是否用钱包wallet_use；日期含首尾。"
                           "每组返回实际天数(days_in_scope/days_with_orders)和按有订单日计算的日均，周/月标记是否完整。"
                           "少于20位可确认会员的组不返回，只给出被隐藏组数；不是完整排名或因果分析。",
                           {"type": "object", "properties": {
                               "by": {"type": "string", "enum": list(research.GROUPS)},
                               "start_date": DATE_PARAMETER, "end_date": DATE_PARAMETER},
                            "required": ["by", "start_date", "end_date"], "additionalProperties": False},
                           validate_aggregate, lambda p, q: call("query_aggregate", p, q)))

    def validate_products(p):
        allowed_fields(p, {"group", "period", "product", "start_date", "end_date", "limit"},
                       {"group", "period", "product", "start_date", "end_date"})
        if p["group"] not in research.PRODUCT_GROUPS or p["period"] not in research.PERIODS:
            raise ToolError("group只能是product/product_spec，period只能是none/day/week/month。")
        if p["product"] is not None and (not isinstance(p["product"], str) or not 0 < len(p["product"]) <= 100):
            raise ToolError("product必须是1–100字的商品名称或null。")
        limit = p.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ToolError("limit必须为1–200的整数。")
        validate_dates(p)

    registry.register(Tool("query_products",
                           "只读商品级聚合：按商品或商品+规格汇总记录件数、含该商品的订单数、明细应付/退款/系统净额和单件净额；"
                           "period可按day/week/month展开成时间序列，product可筛选单个商品（来源原名）。"
                           "每组需至少20位可确认会员；件数不扣除退款件数，单件净额不是菜单价。",
                           {"type": "object", "properties": {
                               "group": {"type": "string", "enum": list(research.PRODUCT_GROUPS)},
                               "period": {"type": "string", "enum": list(research.PERIODS)},
                               "product": {"type": ["string", "null"], "description": "商品原名；不限为null"},
                               "start_date": DATE_PARAMETER, "end_date": DATE_PARAMETER,
                               "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "默认50"}},
                            "required": ["group", "period", "product", "start_date", "end_date"],
                            "additionalProperties": False},
                           validate_products, lambda p, q: call("query_products", p, q)))

    def validate_factors(p):
        allowed_fields(p, {"target", "start_date", "end_date", "business_context"},
                       {"target", "start_date", "end_date"})
        if not isinstance(p["target"], str) or p["target"] not in {"all", "sales", "order_value", "retention"}:
            raise ToolError("target只支持all/sales/order_value/retention。")
        validate_aggregate({"by": "all", "start_date": p["start_date"], "end_date": p["end_date"]})
        context = p.get("business_context")
        if context is not None and (not isinstance(context, str) or len(context) > 800):
            raise ToolError("business_context只能为最多800字的文本或null。")

    registry.register(Tool("inspect_factors",
                           "检查解释订单量、单均净额或留存的候选因素。按项目假设映射真实字段，返回缺失数据、"
                           "方法建议和当前受限聚合证据。不会发现已确认原因、做回归或重要性排名；业务背景为待核实信息。",
                           {"type": "object", "properties": {
                               "target": {"type": "string", "enum": ["all", "sales", "order_value", "retention"]},
                               "start_date": {"type": ["string", "null"], "description": "YYYY-MM-DD；不限为null"},
                               "end_date": {"type": ["string", "null"], "description": "YYYY-MM-DD；不限为null"},
                               "business_context": {"type": ["string", "null"], "maxLength": 800,
                                                    "description": "用户提供、尚未核实的业务背景；未提供为null"}},
                            "required": ["target", "start_date", "end_date"], "additionalProperties": False},
                           validate_factors, lambda p, q: call("inspect_factors", p, q)))

    analyses = [name for name in research.READINESS
                if name not in {"describe_data", "query_aggregate", "query_products", "inspect_factors"}]

    def validate_readiness(p):
        allowed_fields(p, {"analysis"}, {"analysis"})
        if not isinstance(p["analysis"], str) or p["analysis"] not in analyses:
            raise ToolError("analysis必须为工具定义中列出的研究方法。")

    registry.register(Tool("check_analysis_readiness",
                           "检查储值、留存、A/B、回归、功效计算或报告的可用性。"
                           "当前仅返回具体缺失数据或待实现状态，不执行这些研究，不生成替代数字。",
                           {"type": "object", "properties": {"analysis": {"type": "string", "enum": analyses}},
                            "required": ["analysis"], "additionalProperties": False},
                           validate_readiness, lambda p, q: call(p["analysis"], {}, q)))
    registry.instructions = LIVE_INSTRUCTIONS
    return registry


LIVE_INSTRUCTIONS = """数据集：飞书每日同步的最近成功版本（不是实时API）。
- 披露规则：每组至少20位可确认会员；未返回的组不代表零，可引用suppressed_group_count。
- 口径：支付观察含全额退款单、排除明确测试；系统支付净额含钱包支付，不是利润或银行到账。
- 订单笔数不等于商品件数；商品问题用query_products。钱包支付不能识别储值用户。
- 问影响因素或解释变量时用inspect_factors：候选因素不是已验证原因，按factor_count和readiness_counts报告。
- 储值、留存、A/B、回归等可行性用check_analysis_readiness。
- 区分来源核验时间、数据截至时间和今天；freshness_status为check_overdue时要说明核验已过期。"""
