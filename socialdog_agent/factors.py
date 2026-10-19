"""Evidence-backed candidate-factor audit, not causal discovery or regression.

Hypotheses are maintained from the project research directions. Field presence
is checked against the selected snapshot's inventory; the four implemented
group comparisons use the existing privacy-limited local query function.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html import escape
from pathlib import Path
import sqlite3

import research_assistant as research

FACTOR_VERSION = "0.1.0"
TARGETS = {"all": "全部候选研究方向", "sales": "订单量（当前只观察支付订单，不等于商品销量或总需求）",
           "order_value": "单均系统支付净额（含退款，不等于标价或利润）", "retention": "复购与留存"}


@dataclass(frozen=True)
class Requirement:
    label: str
    tables: tuple[str, ...]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class Factor:
    id: str
    name: str
    targets: tuple[str, ...]
    hypothesis: str
    requirements: tuple[Requirement, ...]
    method: str
    cautions: tuple[str, ...]
    group_by: str | None = None


def order_field(label, *aliases):
    return Requirement(label, ("订单数据",), aliases)


DEMAND = ("sales", "order_value")
ALL = ("sales", "order_value", "retention")
CATALOG = (
    Factor("hour", "下单时段", DEMAND, "不同下单小时的订单观察量或单均净额是否不同？",
           (order_field("有效下单时间", "下单时间"),),
           "按下单小时描述累计支付单量、净额和单均净额。",
           ("只显示达到人数门槛的小时组，不能进行全时段排名。", "需要营业小时、营业天数和客流才能比较需求强度或转化率。"), "hour"),
    Factor("weekday", "星期与周内节律", DEMAND, "观察期内不同星期的累计订单和单均净额是否不同？",
           (order_field("有效下单时间", "下单时间"),),
           "按星期描述观察期累计支付单量和净额。",
           ("累计量不是日均量；各星期营业天数、部分日和缺失日期须核实。", "不能据此推断星期本身造成销量变化。"), "weekday"),
    Factor("delivery", "自取与配送方式", DEMAND, "不同履约方式的订单构成和单均净额是否不同？",
           (order_field("配送方式", "配送方式"),),
           "按配送方式描述支付观察的订单构成和单均净额。",
           ("字段存在不保证已填完整或可选方式一致，需确认未记录值。", "顾客选择、配送费、距离和商品组合可能共同变化，不能认定配送导致消费差异。"), "delivery"),
    Factor("sales_source", "销售来源与渠道", DEMAND, "不同销售来源的订单构成和单均净额是否不同？",
           (order_field("销售来源", "销售来源"),),
           "按销售来源描述支付观察的累计单量和净额。",
           ("需确认渠道编码、曝光和营业边界；来源不等于获客归因。", "不能将渠道订单差异直接解释为推广效果。"), "sales_source"),
    Factor("product_mix", "商品与规格组合", DEMAND, "商品、规格或冷热饮构成是否对应不同的订单结构？",
           (Requirement("订单商品明细", ("商品明细",), ("历史商品名称", "商品名称")),
            Requirement("商品数量", ("商品明细",), ("数量", "商品数量")),
            Requirement("商品与订单关联", ("商品明细",), ("关联订单", "匿名订单编号"))),
           "实现商品级受限聚合后，核对数量单位、退款与商品组合；必要时再建立模型。",
           ("当前只读分析库未加载商品明细，表头存在不代表当前工具能计算商品销量。", "不能用订单笔数替代商品件数；替代关系需要价格及可售状态历史。")),
    Factor("price", "价格与价格调整", DEMAND, "真实执行价格变化是否与订单或商品销量变化有关？",
           (Requirement("商品目录价格", ("商品价格快照",), ("目录售价", "商品售价", "价格")),
            Requirement("真实价格执行记录", ("价格执行事件", "商品价格历史"), ("执行价格", "调整后价格")),
            Requirement("调价生效时间", ("价格执行事件", "商品价格历史"), ("生效时间", "执行时间"))),
           "补齐价格执行事件和商品销量，再由教授确定识别策略与回归设定。",
           ("当前价格快照不等于历史调价记录；实付差异也可能来自优惠或商品组合。", "不输出价格弹性或调价因果效果。")),
    Factor("coupon", "优惠券设计与发放", ALL, "优惠券规则或发放是否对应新增消费与后续复购？",
           (order_field("订单优惠金额", "优惠券减免"),
            Requirement("活动与资格记录", ("券与实验", "优惠券发放"), ("活动ID", "实验ID")),
            Requirement("分配组记录", ("券与实验", "实验分配"), ("随机分组", "分配组")),
            Requirement("发放或曝光时间", ("券与实验", "优惠券发放"), ("发放时间", "曝光时间"))),
           "先登记主要指标、观察窗口与样本量，确认随机分配后按分配组进行意向处理分析。",
           ("优惠抵扣字段不能代替发放、资格和随机分配；用券与不用券的比较可能存在选择偏差。", "券效果不能直接外推为菜单降价效果；当前未执行A/B分析。")),
    Factor("stored_value", "储值与余额", ALL, "充值、赠送与余额状态是否对应消费频率或单均净额差异？",
           (order_field("钱包支付构成", "钱包支付额"),
            Requirement("充值本金", ("储值流水", "充值与余额流水"), ("充值本金", "充值金额")),
            Requirement("赠送与余额记录", ("储值流水", "充值与余额流水"), ("赠送金额",)),
            Requirement("余额变动及期初余额", ("储值流水", "充值与余额流水"), ("余额", "账户余额", "期初余额"))),
           "核对账户关联、本金与赠送拆分、期初期末余额，并预先定义储值用户和观察窗口。",
           ("钱包支付额不能识别储值用户或充值金额。", "储值人群可能本来消费更频繁，描述差异不等于储值产生的效果。")),
    Factor("cohort", "用户进入时间与入学队列", ("retention",), "不同注册或入学队列的留存和消费曲线是否不同？",
           (order_field("订单中的关联标识", "匿名顾客编号"),
            Requirement("用户注册或入学时间", ("用户主表", "用户"), ("入学时间", "注册时间")),
            Requirement("用户主表稳定关联键", ("用户主表", "用户"), ("匿名顾客编号", "匿名ID"))),
           "确定队列定义、稳定用户关联和留存窗口，核对每队列后续订单覆盖后计算留存。",
           ("当前首次观察到下单不等于真实首单或入学；注册月份不等于入学月份。", "顾客标识缺失、游客标识不稳定和观察期短会限制留存分析。")),
    Factor("weather", "天气", ("sales",), "气温或降雨是否与订单观察量、冷热饮构成有关？",
           (Requirement("按日天气记录", ("天气", "外部变量", "按日因素"), ("气温", "降雨量", "天气")),
            Requirement("天气日期", ("天气", "外部变量", "按日因素"), ("日期", "观测日期"))),
           "确认门店位置与天气观测点，按日期对齐，再核对营业暴露和相关控制变量。",
           ("需要可追溯的天气来源；当前没有自动获取天气或估计天气效应。", "日期、季节与校历可能共同变化，相关不等于因果。")),
    Factor("calendar", "校历与校园活动", ("sales", "retention"), "考试周、开学或校园活动是否与订单变化有关？",
           (Requirement("校历事件", ("校历", "校园活动", "外部变量", "按日因素"), ("考试周", "开学日", "校园活动", "事件类型")),
            Requirement("事件日期", ("校历", "校园活动", "外部变量", "按日因素"), ("日期", "开始日期"))),
           "用官方校历或经核实的事件记录对齐营业日期，预先确定比较窗口。",
           ("需要确认学校、事件与日期，不能由模型补造考试周。", "假期、天气、营业时长等可能同时变化。")),
    Factor("waiting", "等待与制作时间", ("sales", "retention"), "等待时间是否对应取消、履约方式变化或后续消费？",
           (order_field("实际制作开始时间", "制作开始时间", "实际制作开始"),
            order_field("实际出杯或完成时间", "出杯时间", "制作完成时间", "取餐完成时间")),
           "先核实时戳含义与缺失，补充排队、取消及未成交观察，再设计等待与需求研究。",
           ("订单支付时间或取餐完成来源标签不能替代实际制作时间戳。", "只看到成交订单，不能直接推断放弃购买的人数或损失需求。")),
    Factor("points", "积分与兑换门槛", ("retention", "order_value"), "接近积分兑换门槛时的消费频率是否不同？",
           (order_field("订单积分抵扣", "积分抵扣"),
            Requirement("积分获取与兑换流水", ("积分流水",), ("积分变动", "积分增减")),
            Requirement("积分兑换时间", ("积分流水",), ("兑换时间", "变动时间"))),
           "补齐账户积分余额、获取兑换流水与规则历史，再定义离门槛距离和研究窗口。",
           ("积分抵扣金额不能替代积分余额或兑换门槛。", "当前未估计目标梯度效应。")),
)


def inspect_factors(manifest: dict, connection: sqlite3.Connection, *, target: str = "all",
                    start_date: str | None = None, end_date: str | None = None,
                    business_context: str | None = None) -> dict:
    if not isinstance(target, str) or target not in TARGETS:
        raise research.AnalysisError("target只支持all/sales/order_value/retention。")
    start_date, end_date = research.valid_date(start_date), research.valid_date(end_date)
    if start_date and end_date and start_date > end_date:
        raise research.AnalysisError("开始日期不能晚于结束日期。")
    if business_context is not None and (not isinstance(business_context, str) or len(business_context) > 800):
        raise research.AnalysisError("业务背景只能是最多800字的文本或null。")
    inventory = {t["name"]: set(t["fields"]) for t in manifest["source_tables"]}
    nonempty = {t["name"]: t.get("row_count", 0) > 0 for t in manifest["source_tables"]}
    factors = []
    for spec in CATALOG:
        if target != "all" and target not in spec.targets:
            continue
        evidence = []
        for required in spec.requirements:
            matched = [{"table": table, "field": field, "table_has_records": nonempty.get(table, False)} for table in required.tables
                       for field in required.fields if field in inventory.get(table, set())]
            presence = "missing" if not matched else "present" if any(m["table_has_records"] for m in matched) else "present_but_empty"
            evidence.append({"requirement": required.label, "presence": presence,
                             "matched_fields": matched, "value_completeness": "not_profiled"})
        missing = [e["requirement"] for e in evidence if e["presence"] != "present"]
        item = {"id": spec.id, "name": spec.name, "hypothesis": spec.hypothesis,
                "basis": "项目研究方向形成的候选假设，已对照本快照字段；尚未验证影响",
                "schema_evidence": evidence, "missing_data": missing,
                "readiness": "missing_data" if missing else "tool_not_implemented",
                "suggested_method": spec.method, "limitations": list(spec.cautions),
                "causal_status": "not_estimated", "importance_rank": None}
        if not missing and spec.group_by:
            parameters = {"by": spec.group_by, "start_date": start_date, "end_date": end_date}
            result = research.query_aggregate(connection, **parameters)
            item["query_evidence"] = {"tool": "query_aggregate", "parameters": parameters,
                                      "result": result}
            item["readiness"] = "descriptive_ready" if result["status"] == "ok" else "no_publishable_groups"
            item["released_group_count"] = len(result["rows"])
            item["can_compare_released_groups"] = len(result["rows"]) >= 2
            if len(result["rows"]) == 1:
                item["limitations"].append("当前只披露一个分组，只能描述该组，不能比较不同组的差异。")
        factors.append(item)
    counts = {status: sum(f["readiness"] == status for f in factors) for status in
              ["descriptive_ready", "no_publishable_groups", "missing_data", "tool_not_implemented"]}
    return {"status": "ok", "factor_tool_version": FACTOR_VERSION, "target": target,
            "target_definition": TARGETS[target], "factor_count": len(factors), "factors": factors,
            "readiness_counts": counts, "analysis_status": "candidate_audit_not_effect_estimation",
            "candidate_origin": "按项目PRD研究方向人工维护候选定义，动态核验字段与受限聚合；不是自动因果发现",
            "candidate_order": "按研究类别排列，不表示影响大小或重要性排序",
            "query_parameters": {"start_date": start_date, "end_date": end_date},
            "data_overview": {k: manifest[k] for k in ["snapshot_as_of", "observation_start", "last_order_at",
                              "as_of_day_is_partial", "orders_in_source"] if k in manifest} |
                             {"observation_calendar_days": research.observation_calendar_days(manifest)},
            "user_context": {"text": business_context, "evidence_status": "user_supplied_unverified"},
            "trend_assessed": False, "statistical_tests_performed": 0,
            "limitations": ["字段存在只说明来源表头存在，不保证非空、跨期稳定或已进入当前分析库。",
                             "候选因素不是已确认原因；未执行回归、显著性检验、效应估计或重要性排序。",
                             "未核验销量上升或下降；确认趋势须先定义可比窗口、营业暴露及指标。",
                             "查询证据只包含达到人数门槛的分组，隐藏组不代表零，展示分组合计不等于总计。",
                             "用户提供的业务背景仍需通过业务记录核实。"]}


STATUS_LABELS = {"descriptive_ready": "可查询受限描述性统计", "no_publishable_groups": "当前无可发布分组",
                 "missing_data": "缺少关键数据", "tool_not_implemented": "有相关字段，工具待实现"}


def to_markdown(result: dict) -> str:
    def cell(value):
        return escape(str(value)).replace("|", "\\|").replace("\n", " ")
    lines = ["# SocialDog 候选因素与数据可用性检查", "", f"快照版本：`{result['snapshot_version']}`。",
             f"来源数据截至：{result['data_overview'].get('snapshot_as_of', '未记录')}。",
             f"研究目标：{result['target_definition']}。候选方向：{result['factor_count']} 项。", "",
             "本清单按项目研究方向维护候选定义，并根据当前字段与受限查询更新可用性。未进行因果发现、回归或重要性排序。", "",
             "| 候选因素 | 当前状态 | 实际存在的字段依据 | 缺少的数据 |",
             "| --- | --- | --- | --- |"]
    if result.get("data_source"):
        source = result["data_source"]
        lines[6:6] = ["", f"最近来源核验：{source['source_checked_at']}。采用飞书每日同步的最近成功版本。",
                      source["notice"], ""]
    for factor in result["factors"]:
        fields = [f"{m['table']}.{m['field']}" for e in factor["schema_evidence"] for m in e["matched_fields"]]
        lines.append("| " + " | ".join(cell(v) for v in [factor["name"], STATUS_LABELS[factor["readiness"]],
                     "；".join(fields) or "尚无对应字段", "；".join(factor["missing_data"]) or "仍需核实填充、口径及方法条件"]) + " |")
    for factor in result["factors"]:
        lines += ["", f"## {factor['name']}", "", factor["hypothesis"], "", "验证路径：" + factor["suggested_method"]]
        lines += ["- " + note for note in factor["limitations"]]
        if "query_evidence" in factor:
            check = factor["query_evidence"]
            lines += ["", "本轮受限查询参数：`" + research.encoded(check["parameters"]).replace("\n", " ") + "`。",
                      f"已披露分组：{len(check['result']['rows'])} 个；仅作描述性证据，不表示该因素已被证实。"]
            if check["result"]["rows"]:
                lines += ["", "| 已披露分组 | 支付观察单量 | 系统支付净额（元） | 单均净额（元） |",
                          "| --- | ---: | ---: | ---: |"]
                for row in check["result"]["rows"]:
                    lines.append("| " + " | ".join(cell(v) for v in [row["group"], row["paid_observed_orders"],
                                 f"{row['system_net_yuan']:.2f}", f"{row['net_per_observed_order_yuan']:.2f}"]) + " |")
    lines += ["", "## 共同限制", ""] + ["- " + note for note in result["limitations"]]
    lines += ["- 本次统计检验次数：0。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查飞书数据中的候选因素与研究缺口，不调用LLM")
    parser.add_argument("--snapshot", type=Path, help="显式使用固定快照；默认最近成功飞书批次")
    parser.add_argument("--target", choices=TARGETS, default="all")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--export-dir", type=Path, help="新建目录，保存Markdown和JSON检查结果")
    args = parser.parse_args()
    from .feishu_source import FeishuSnapshotSource
    snapshot = args.snapshot
    source_info = {}
    if snapshot is None:
        snapshot, source_info = FeishuSnapshotSource().resolve()
    result = research.run_tool(snapshot, "inspect_factors", {"target": args.target,
            "start_date": args.start_date, "end_date": args.end_date})
    if result["status"] != "ok":
        print(research.encoded({"status": "error", "error": "因素可用性检查未完成，请核查本地日志。"}))
        return 1
    if source_info:
        result["data_source"] = source_info
    if args.export_dir:
        args.export_dir.mkdir(parents=True, exist_ok=False)
        (args.export_dir / "候选因素与数据缺口.md").write_text(to_markdown(result), encoding="utf-8")
        (args.export_dir / "factor_inspection.json").write_text(research.encoded(result), encoding="utf-8")
    print(research.encoded({"status": "ok", "snapshot_version": result["snapshot_version"],
                          "target": result["target"], "factor_count": result["factor_count"],
                          "readiness_counts": result["readiness_counts"], "statistical_tests_performed": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
