import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Evidence, IssueDraft, Stage } from "../api/types";
import { errorText } from "../lib/format";
import { Button, Field, Notice, Select, Textarea } from "./ui";

export const STAGES: Record<Stage, string> = { retrieval: "取数", definition: "口径", calculation: "计算", reasoning: "推理", conclusion: "结论", other: "其他" };
export const REVIEW_LABELS = { pending: "待复核", confirmed: "成立", rejected: "不成立", discuss: "待讨论" };
export const newIssue = (): IssueDraft => ({ stage: "retrieval", description: "", correct_approach: "", evidence_refs: [] });

export function IssueFields({ value, onChange, evidence }: { value: IssueDraft; onChange: (v: IssueDraft) => void; evidence: Evidence[] }) {
  return <div className="space-y-4">
    <Field label="出问题的环节"><Select value={value.stage} onValueChange={e => onChange({ ...value, stage: e as Stage })}>{Object.entries(STAGES).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</Select></Field>
    <Field label="问题描述"><Textarea required maxLength={4000} rows={3} value={value.description} onChange={e => onChange({ ...value, description: e.target.value })} /></Field>
    <Field label="正确的做法或应有结果（选填）"><Textarea maxLength={4000} rows={2} value={value.correct_approach} onChange={e => onChange({ ...value, correct_approach: e.target.value })} /></Field>
    {!!evidence.length && <fieldset><legend className="mb-2 text-sm font-medium">关联依据（选填）</legend><div className="space-y-2">{evidence.map((item, i) => <label key={item.call_id} className="flex items-start gap-2 text-sm break-all"><input type="checkbox" className="mt-1" checked={value.evidence_refs.includes(item.call_id)} onChange={e => onChange({ ...value, evidence_refs: e.target.checked ? [...value.evidence_refs, item.call_id] : value.evidence_refs.filter(v => v !== item.call_id) })} /><span>依据 {i + 1} · {item.tool}<code className="block text-xs text-muted">{item.arguments}</code></span></label>)}</div></fieldset>}
  </div>;
}

export function IssueListEditor({ issues, onChange, evidence }: { issues: IssueDraft[]; onChange: (v: IssueDraft[]) => void; evidence: Evidence[] }) {
  return <div className="my-6 space-y-4"><div className="flex items-center justify-between gap-3"><h3 className="font-medium">问题反馈（选填）</h3><Button disabled={issues.length >= 20} onClick={() => onChange([...issues, newIssue()])}>添加问题</Button></div>{issues.map((issue, index) => <fieldset key={index} className="rounded-lg border border-line p-4"><legend className="px-2 text-sm">问题 {index + 1}</legend><IssueFields value={issue} evidence={evidence} onChange={v => onChange(issues.map((x, i) => i === index ? v : x))} /><Button className="mt-3" variant="ghost" onClick={() => onChange(issues.filter((_, i) => i !== index))}>移除此问题</Button></fieldset>)}</div>;
}

export function FeedbackButton({ turnId, evidence, attemptId }: { turnId: string; evidence: Evidence[]; attemptId?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState(newIssue);
  const client = useQueryClient();
  const submit = useMutation({ mutationFn: () => api.feedback({ ...value, turn_id: turnId, source: attemptId ? "evaluation" : "chat", attempt_id: attemptId ?? null }), onSuccess: () => { client.invalidateQueries({ queryKey: ["my-feedback"] }); } });
  return <>
    <button type="button" className="copy-answer" onClick={() => { setValue(newIssue()); submit.reset(); dialog.current?.showModal(); }}>这条回答有问题</button>
    <dialog ref={dialog} className="feedback-dialog" aria-label="提交问题反馈" onClick={e => { if (e.target === e.currentTarget) dialog.current?.close(); }}>
      <div className="flex items-center justify-between gap-4 mb-6"><h2 className="text-xl font-semibold">问题反馈</h2><Button variant="ghost" onClick={() => dialog.current?.close()}>关闭</Button></div>
      {submit.isSuccess ? <Notice tone="ok">反馈已提交，可在“我的研究”查看复核结果。</Notice> : <form onSubmit={e => { e.preventDefault(); if (value.description.trim()) submit.mutate(); }}><IssueFields value={value} onChange={setValue} evidence={evidence} />{submit.error && <div className="mt-4"><Notice>{errorText(submit.error)}</Notice></div>}<div className="mt-6 flex justify-end"><Button type="submit" variant="primary" disabled={!value.description.trim() || submit.isPending}>{submit.isPending ? "正在提交…" : "提交反馈"}</Button></div></form>}
    </dialog>
  </>;
}
