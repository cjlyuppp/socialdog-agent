const zone = "Asia/Shanghai";

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(parsed);
}

export function relative(value: string | null | undefined): string {
  if (!value) return "从未";
  const minutes = Math.round((Date.now() - new Date(value).valueOf()) / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} 小时前`;
  return dateTime(value).slice(0, 10);
}

export const integer = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN");
export const yuan = (value: number | null) => value === null ? "—" :
  value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function thisMonth(): string {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: zone, year: "numeric", month: "2-digit" }).format(new Date());
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误，请重试。";
}
