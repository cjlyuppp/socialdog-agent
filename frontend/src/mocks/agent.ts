// A scripted stand-in for the DeepSeek agent: picks a tool by keyword and answers from
// pre-cutoff data only, so mock answers can be checked on the 数据核对 page.
import type { AgentResult, Evidence, ReferenceGroup } from "../api/types";
import { AGENT_LINES, CUTOFF, DATASET_VERSION, addDays, aggregate } from "./data";

const yuan = (value: number) => value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (a: number, b: number) => Math.round(((b - a) / a) * 10000) / 100;

function evidence(tool: string, args: object, result: Record<string, unknown>, index: number): Evidence {
  return {
    tool, call_id: `call_${index}`, arguments: JSON.stringify(args),
    result: { status: "ok", snapshot_version: DATASET_VERSION, statistical_tests_performed: 0, ...result },
  };
}

function describe(index: number): Evidence {
  const first = AGENT_LINES[0].order_date;
  return evidence("history_describe", {}, {
    observation_start: first, observation_end: CUTOFF, as_of: CUTOFF,
    source_order_count: AGENT_LINES.length, privacy: { minimum_orders_per_group: 5 },
  }, index);
}

function grouped(by: ReferenceGroup, title: string): { answer: string; evidence: Evidence[] } {
  const args = { scope: "baseline" as const, by, start_date: null, end_date: null, product: null };
  const result = aggregate(args, AGENT_LINES);
  const rows = result.rows.map(({ after_cutoff: _hidden, ...row }) => row);
  const top = rows.slice(0, 24);
  const table = ["| 分组 | 订单数 | 记录件数 | 记录收款（元） |", "| --- | ---: | ---: | ---: |",
    ...top.map((r) => `| ${r.group} | ${r.order_count} | ${r.recorded_quantity} | ${yuan(r.recorded_receipt_yuan)} |`)];
  const answer = [
    `以下是基准样本${title}的汇总（数据截至 ${CUTOFF}，金额为记录收款金额）：`, "", ...table,
    rows.length > top.length ? `\n共 ${rows.length} 组，上表只列出前 ${top.length} 组。` : "",
    "", "**口径与限制**",
    "- 记录收款金额不含余额扣除，不等于净收入或利润。",
    "- 每组至少 5 个订单才披露；未出现的分组不代表零。",
    by === "product" ? "- 不同商品的订单数会重叠，不能相加为全店订单数。" : "",
  ].filter((line) => line !== "").join("\n");
  return { answer, evidence: [describe(1), evidence("history_aggregate", args, { rows, notes: result.notes }, 2)] };
}

function window(product: string, start: string, end: string) {
  const items = AGENT_LINES.filter((l) => l.baseline && l.product === product && l.order_date >= start && l.order_date <= end);
  const days = new Set(AGENT_LINES.filter((l) => l.order_date >= start && l.order_date <= end).map((l) => l.order_date)).size;
  const quantity = items.reduce((s, l) => s + l.quantity, 0);
  const receipt = items.reduce((s, l) => s + l.receipt, 0);
  return {
    start, end, days_with_orders: days, product_quantity: quantity, product_receipt_yuan: receipt,
    product_quantity_per_day_with_orders: Math.round((quantity / days) * 100) / 100,
    product_receipt_per_day_with_orders_yuan: Math.round((receipt / days) * 100) / 100,
  };
}

function priceEvent(): { answer: string; evidence: Evidence[] } {
  const date = "2023-03-01";
  const before = window("拿铁", addDays(date, -14), addDays(date, -1));
  const after = window("拿铁", date, addDays(date, 13));
  const change = {
    product_quantity_per_day: pct(before.product_quantity_per_day_with_orders, after.product_quantity_per_day_with_orders),
    product_receipt_per_day: pct(before.product_receipt_per_day_with_orders_yuan, after.product_receipt_per_day_with_orders_yuan),
  };
  const event = {
    product: "拿铁", specification: "未记录", event_date: date, old_unit_price_yuan: 18, new_unit_price_yuan: 16,
    price_change_pct: -11.11, direction: "降价", before, after, change_pct: change,
    profit: { status: "unavailable", missing: ["原料、人工及其他成本", "退款金额与收入确认口径"] },
  };
  const answer = [
    `历史数据中（截至 ${CUTOFF}）检测到 1 次持续调价：**拿铁在 ${date} 从 18 元降到 16 元**（-11.11%）。`, "",
    "| 窗口 | 日期 | 有订单天数 | 拿铁件数 | 日均件数 | 日均收款（元） |", "| --- | --- | ---: | ---: | ---: | ---: |",
    `| 降价前 14 天 | ${before.start} 至 ${before.end} | ${before.days_with_orders} | ${before.product_quantity} | ${before.product_quantity_per_day_with_orders} | ${yuan(before.product_receipt_per_day_with_orders_yuan)} |`,
    `| 降价后 14 天 | ${after.start} 至 ${after.end} | ${after.days_with_orders} | ${after.product_quantity} | ${after.product_quantity_per_day_with_orders} | ${yuan(after.product_receipt_per_day_with_orders_yuan)} |`,
    "", `降价后日均件数变化 ${change.product_quantity_per_day}%，日均收款变化 ${change.product_receipt_per_day}%。`, "",
    "**这不是价格弹性的估计**：前后对比没有控制开学、季节和天气；2 月下旬寒假结束，客流本身就在回升。",
    "**利润无法回答**：数据中没有原料、人工等成本记录。",
  ].join("\n");
  return { answer, evidence: [describe(1), evidence("history_price_events", { product: null }, { event_count: 1, events: [event] }, 2)] };
}

function unavailable(analysis: string, missing: string[]): { answer: string; evidence: Evidence[] } {
  return {
    answer: `当前历史数据无法回答这个问题。缺少：${missing.join("、")}。\n\n历史数据截至 ${CUTOFF}，不是今天的经营数据。`,
    evidence: [evidence("history_unavailable", { analysis }, { status: "unavailable", analysis, missing }, 1)],
  };
}

export function runMockAgent(question: string): AgentResult & { usage: { input_tokens: number; output_tokens: number } } {
  const q = question;
  const picked =
    // Price questions first: the price answer already explains why profit is unavailable.
    /调价|降价|涨价|价格/.test(q) ? priceEvent()
    : /利润/.test(q) ? unavailable("profit", ["可核验的原料、人工及其他成本", "退款及收入确认口径"])
    : /今天|今日/.test(q) ? unavailable("today", ["当前日期的经营数据；本包只含历史记录"])
    : /按月|每月|月度/.test(q) ? grouped("month", "按月")
    : /按周|每周/.test(q) ? grouped("week", "按周")
    : /星期|周几/.test(q) ? grouped("weekday", "按星期（星期一=1）")
    : /小时|时段/.test(q) ? grouped("hour", "按下单小时")
    : /商品|产品|卖得/.test(q) ? grouped("product", "按商品")
    : grouped("all", "总计");
  return {
    ...picked, snapshot_versions: [DATASET_VERSION], model: "deepseek-flash（模拟）", reasoning_effort: "high",
    grounding: { ungrounded_numbers: [], repair_attempted: false },
    request_id: crypto.randomUUID().replaceAll("-", ""),
    usage: { input_tokens: 5200 + q.length * 3, output_tokens: 380 + picked.answer.length },
  };
}
