import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ReferenceQuery, ReferenceResult } from "../api/types";
import { errorText, integer, yuan } from "../lib/format";
import { Badge, Button, Empty, Field, Input, Notice, Select, Spinner } from "./ui";
import { Icon } from "./Icon";
import { useMe } from "../lib/auth";

const DEFAULT_QUERY: ReferenceQuery = { scope: "baseline", by: "month", start_date: null, end_date: null, product: null };

function downloadCsv(result: ReferenceResult) {
  const header = ["分组", "订单数", "明细数", "记录件数", "记录收款(元)", "记录成交(元)", "余额扣除(元)", "优惠券(元)", "截断后"];
  const rows = result.rows.map((r) => [r.group, r.order_count, r.line_count, r.recorded_quantity, r.recorded_receipt_yuan,
                                       r.recorded_deal_yuan, r.wallet_yuan, r.coupon_yuan, r.after_cutoff ? "是" : ""]);
  const text = "﻿" + [header, ...rows].map((r) => r.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(",")).join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  link.download = `数据核对_${result.query.scope}_${result.query.by}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function ReferenceExplorer({ initial, compact = false, context = {} }: { initial?: Partial<ReferenceQuery> | null; compact?: boolean; context?: Partial<Pick<ReferenceQuery, "attempt_id" | "assignment_id" | "bank_version_id">> }) {
  const me = useMe().data!;
  const access = [me.id, me.role, "access-v3", context];
  const meta = useQuery({ queryKey: ["reference-meta", ...access], queryFn: () => api.referenceMeta(context) });
  const [draft, setDraft] = useState<ReferenceQuery>({ ...DEFAULT_QUERY, ...initial });
  const [submitted, setSubmitted] = useState<ReferenceQuery>({ ...DEFAULT_QUERY, ...initial });
  const result = useQuery({ queryKey: ["reference", ...access, submitted], queryFn: () => api.reference({ ...submitted, ...context }) });
  const set = <K extends keyof ReferenceQuery>(key: K, value: ReferenceQuery[K]) => setDraft({ ...draft, [key]: value });
  const cutoff = meta.data?.cutoff_date;
  const hiddenRows = result.data?.rows.filter((r) => r.after_cutoff).length ?? 0;

  return (
    <div className="space-y-4">
      <form onSubmit={(e) => { e.preventDefault(); setSubmitted(draft); }}
            className={`grid gap-4 ${compact ? "grid-cols-2" : "grid-cols-2 md:grid-cols-3 xl:grid-cols-6"}`}>
        <Field label="样本">
          <Select value={draft.scope} onValueChange={(e) => set("scope", e as ReferenceQuery["scope"])}>
            <option value="baseline">基准样本</option>
            <option value="all">全部记录</option>
          </Select>
        </Field>
        <Field label="分组">
          <Select value={draft.by} onValueChange={(e) => set("by", e as ReferenceQuery["by"])}>
            {(meta.data?.groups ?? [{ value: "month", label: "按月" }]).map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
          </Select>
        </Field>
        <Field label="开始日期">
          <Input type="date" value={draft.start_date ?? ""} min={meta.data?.observation_start} max={meta.data?.observation_end}
                 onChange={(e) => set("start_date", e.target.value || null)} />
        </Field>
        <Field label="结束日期">
          <Input type="date" value={draft.end_date ?? ""} min={meta.data?.observation_start} max={meta.data?.observation_end}
                 onChange={(e) => set("end_date", e.target.value || null)} />
        </Field>
        <Field label="商品">
          <Select value={draft.product ?? ""} onValueChange={(e) => set("product", e || null)}>
            <option value="">全部商品</option>
            {meta.data?.products.map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
        </Field>
        <div className="flex items-end gap-2">
          <Button type="submit" variant="primary" className="flex-1">查询</Button>
        </div>
      </form>

      {meta.error ? <Notice>{errorText(meta.error)}</Notice> : result.isPending ? <Spinner /> : result.error ? <Notice>{errorText(result.error)}</Notice> : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 pt-2 text-sm leading-6 text-muted">
            <span>
              共 {result.data.rows.length} 组
              {cutoff && hiddenRows > 0 && <> · <span className="text-accent">{hiddenRows} 组包含截止日后数据</span></>}
            </span>
            {result.data.rows.length > 0 && <Button variant="ghost" onClick={() => downloadCsv(result.data)}>导出 CSV</Button>}
          </div>
          {result.data.rows.length === 0 ? <Empty>没有匹配的记录，试试其他日期或商品。</Empty> : (
            <div className={`overflow-auto rounded-lg border border-line bg-surface ${compact ? "max-h-80" : "max-h-[60vh]"}`}>
              <table className="min-w-full text-sm whitespace-nowrap">
                <thead className="sticky top-0 bg-sidebar text-muted">
                  <tr>
                    {["分组", "订单数", "记录件数", "记录收款(元)", "记录成交(元)", ...(compact ? [] : ["余额扣除(元)", "优惠券(元)"])].map((h, i) => (
                      <th key={h} className={`px-4 py-3 font-medium ${i ? "text-right" : "text-left"}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.data.rows.map((row) => (
                    <tr key={`${row.group}:${row.after_cutoff}`} className={`border-t border-[#eef0e8] ${row.after_cutoff ? "bg-accent-soft/40" : ""}`}>
                      <td className="px-4 py-3">{row.group} {row.after_cutoff && <Badge tone="accent">截止日后</Badge>}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{integer(row.order_count)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{integer(row.recorded_quantity)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{yuan(row.recorded_receipt_yuan)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{yuan(row.recorded_deal_yuan)}</td>
                      {!compact && <td className="px-4 py-3 text-right tabular-nums">{yuan(row.wallet_yuan)}</td>}
                      {!compact && <td className="px-4 py-3 text-right tabular-nums">{yuan(row.coupon_yuan)}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details className="query-notes">
            <summary><Icon name="chevron" size={15} />数据说明</summary>
            <ul>
              {cutoff && <li>{meta.data?.can_view_after_cutoff ? (me.role === "admin" ? `管理员可核对完整历史；研究助理只能查询 ${cutoff} 及之前的数据。` : `已开放本题指定的后续核对数据。研究助理的回答仍只使用 ${cutoff} 及之前的数据。`) : `可核对 ${cutoff} 及之前的数据。`}</li>}
              <li>没有记录不代表零营业额或停业。</li>
              {result.data.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          </details>
        </>
      )}
    </div>
  );
}
