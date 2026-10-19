// Deterministic synthetic history for the mock backend. Not real business data.
// Two years of orders; the agent may only see dates up to CUTOFF.
import type { ReferenceGroup, ReferenceQuery, ReferenceResult, ReferenceRow } from "../api/types";

export const DATASET_VERSION = "2022-01_mock00000000_history-backtest-1.1_asof2023-06-30";
export const CUTOFF = "2023-06-30";
export const START = "2022-01-01";
export const END = "2023-12-31";

export interface Line {
  order_date: string;
  hour: number;
  weekday: number;
  product: string;
  category: string;
  quantity: number;
  unit_price: number;
  receipt: number;
  deal: number;
  wallet: number;
  coupon: number;
  delivery: string | null;
  payment: string;
  status: string;
  type: string;
  baseline: boolean;
}

// Price schedule: 拿铁 cut before the cutoff; 美式 raised after it (the hidden answer).
const PRODUCTS = [
  { name: "美式", category: "咖啡", weight: 5, price: (d: string) => (d >= "2023-09-01" ? 13 : 12) },
  { name: "拿铁", category: "咖啡", weight: 6, price: (d: string) => (d >= "2023-03-01" ? 16 : 18) },
  { name: "燕麦拿铁", category: "咖啡", weight: 3, price: () => 20 },
  { name: "冷萃", category: "咖啡", weight: 2, price: () => 22 },
  { name: "可颂", category: "烘焙", weight: 2, price: () => 12 },
];

function random(seed: number) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

function pick<T extends { weight: number }>(items: T[], roll: number): T {
  const total = items.reduce((sum, item) => sum + item.weight, 0);
  let acc = 0;
  for (const item of items) {
    acc += item.weight / total;
    if (roll < acc) return item;
  }
  return items[items.length - 1];
}

export function addDays(day: string, days: number): string {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function isoWeekday(day: string): number {
  const w = new Date(day + "T00:00:00Z").getUTCDay();
  return w === 0 ? 7 : w;
}

function generate(): Line[] {
  const rand = random(20230630);
  const lines: Line[] = [];
  for (let day = START; day <= END; day = addDays(day, 1)) {
    const weekday = isoWeekday(day);
    const month = Number(day.slice(5, 7));
    // Closed on some Sundays and during the winter break.
    if ((weekday === 7 && rand() < 0.4) || (month === 2 && Number(day.slice(8)) < 12)) continue;
    const base = (weekday >= 6 ? 22 : 34) + (month >= 9 || month <= 6 ? 6 : -10);
    const orders = Math.max(4, Math.round(base + (rand() - 0.5) * 10));
    for (let i = 0; i < orders; i++) {
      const product = pick(PRODUCTS, rand());
      const quantity = rand() < 0.85 ? 1 : 2;
      const unit = product.price(day);
      const deal = unit * quantity;
      const coupon = rand() < 0.12 ? 3 : 0;
      const wallet = rand() < 0.2 ? deal - coupon : 0;
      const cancelled = rand() < 0.02;
      lines.push({
        order_date: day, hour: 8 + Math.floor(rand() * 12), weekday, product: product.name,
        category: product.category, quantity, unit_price: unit, deal, coupon, wallet,
        receipt: deal - coupon - wallet,
        delivery: rand() < 0.05 ? null : rand() < 0.75 ? "到店自提" : "外卖配送",
        payment: wallet ? "余额" : rand() < 0.7 ? "微信" : "支付宝",
        status: cancelled ? "已取消" : "已完成", type: "产品", baseline: !cancelled,
      });
    }
  }
  return lines;
}

export const LINES = generate();

export const GROUP_LABELS: { value: ReferenceGroup; label: string }[] = [
  { value: "all", label: "总计" }, { value: "day", label: "按日" }, { value: "week", label: "按周" },
  { value: "month", label: "按月" }, { value: "hour", label: "按小时" }, { value: "weekday", label: "按星期" },
  { value: "category", label: "按分类" }, { value: "product", label: "按商品" }, { value: "delivery", label: "按配送方式" },
  { value: "payment", label: "按收款方式" }, { value: "status", label: "按订单状态" }, { value: "type", label: "按交易类型" },
];

function label(line: Line, by: ReferenceGroup): string {
  switch (by) {
    case "all": return "全部";
    case "day": return line.order_date;
    case "week": return addDays(line.order_date, 1 - line.weekday);
    case "month": return line.order_date.slice(0, 7);
    case "hour": return String(line.hour);
    case "weekday": return String(line.weekday);
    case "category": return line.category;
    case "product": return line.product;
    case "delivery": return line.delivery ?? "未记录";
    case "payment": return line.payment;
    case "status": return line.status;
    case "type": return line.type;
  }
}

const round2 = (value: number) => Math.round(value * 100) / 100;

export function aggregate(query: ReferenceQuery, lines: Line[] = LINES): ReferenceResult {
  const groups = new Map<string, Line[]>();
  for (const line of lines) {
    if (query.scope === "baseline" && !line.baseline) continue;
    if (query.start_date && line.order_date < query.start_date) continue;
    if (query.end_date && line.order_date > query.end_date) continue;
    if (query.product && line.product !== query.product) continue;
    const key = label(line, query.by);
    groups.set(key, [...(groups.get(key) ?? []), line]);
  }
  const numeric = query.by === "hour" || query.by === "weekday";
  const rows: ReferenceRow[] = [...groups.entries()]
    .sort(([a], [b]) => (numeric ? Number(a) - Number(b) : a.localeCompare(b)))
    .map(([group, items]) => ({
      group,
      // One synthetic line per order, so lines and orders coincide here.
      order_count: items.length,
      line_count: items.length,
      recorded_quantity: items.reduce((s, l) => s + l.quantity, 0),
      recorded_receipt_yuan: round2(items.reduce((s, l) => s + l.receipt, 0)),
      recorded_deal_yuan: round2(items.reduce((s, l) => s + l.deal, 0)),
      wallet_yuan: round2(items.reduce((s, l) => s + l.wallet, 0)),
      coupon_yuan: round2(items.reduce((s, l) => s + l.coupon, 0)),
      after_cutoff: items.every((l) => l.order_date > CUTOFF),
    }));
  const notes = ["金额为记录收款金额（元），不是净收入或利润。"];
  if (!["all", "day", "week", "month", "hour", "weekday", "delivery", "payment", "status"].includes(query.by)) {
    notes.push("商品、分类或交易类型分组的订单数会重叠，不能相加为全店订单数。");
  }
  return { query, rows, group_order_counts_additive: !["category", "product", "type"].includes(query.by), notes };
}

export const AGENT_LINES = LINES.filter((l) => l.order_date <= CUTOFF);
