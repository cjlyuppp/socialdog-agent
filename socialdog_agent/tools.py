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


def allowed_fields(parameters: dict, allowed: set, required: set = frozenset()) -> None:
    if set(parameters) - allowed or required - set(parameters):
        raise ToolError("工具参数存在未允许字段或缺少必填字段；请按工具定义重试。")


PUBLIC_MANIFEST_FIELDS = {"snapshot_version", "tool_version", "timezone", "snapshot_as_of",
                          "observation_start", "last_order_at", "as_of_day_is_partial",
                          "orders_in_source", "source_tables", "identity_coverage",
                          "public_query_fields", "privacy_min_confirmed_members", "tools", "limitations"}


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
        "user_context", "trend_assessed"}}


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
            raise ToolError("by只能是all/hour/weekday/delivery/sales_source。")
        try:
            for field in ["start_date", "end_date"]:
                research.valid_date(p[field])
        except research.AnalysisError as error:
            raise ToolError(str(error)) from None
        if p["start_date"] and p["end_date"] and p["start_date"] > p["end_date"]:
            raise ToolError("开始日期不能晚于结束日期。")

    registry.register(Tool("query_aggregate",
                           "只读订单聚合。按总计、下单小时、星期、配送方式或销售来源汇总；日期含首尾。"
                           "少于20位可确认会员的组不返回，缺少的组不代表零；不是完整排名或因果分析。",
                           {"type": "object", "properties": {
                               "by": {"type": "string", "enum": list(research.GROUPS)},
                               "start_date": {"type": ["string", "null"], "description": "YYYY-MM-DD；不限为null"},
                               "end_date": {"type": ["string", "null"], "description": "YYYY-MM-DD；不限为null"}},
                            "required": ["by", "start_date", "end_date"], "additionalProperties": False},
                           validate_aggregate, lambda p, q: call("query_aggregate", p, q)))

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

    analyses = [name for name in research.READINESS if name not in {"describe_data", "query_aggregate", "inspect_factors"}]

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
    return registry
