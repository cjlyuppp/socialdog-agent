import { useState } from "react";
import { Link, Navigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { IdeaInput, IdeaStatus, ResearchIdea } from "../api/types";
import { EvidenceList } from "../components/answer";
import { REVIEW_LABELS, STAGES } from "../components/Feedback";
import { Page } from "../components/Layout";
import { Badge, Button, Card, Empty, Field, Notice, PageHeader, Spinner, Textarea } from "../components/ui";
import { can, ME_KEY, useMe } from "../lib/auth";
import { dateTime, errorText } from "../lib/format";

export const IDEA_LABELS: Record<IdeaStatus, string> = { pending: "待审", feedback_given: "已反馈", invited: "邀请加入", not_suitable: "暂不合适" };
export const IDEA_FIELDS = [{ key: "question", label: "研究问题" }, { key: "data_used", label: "用到的数据" }, { key: "findings", label: "初步发现" }, { key: "next_steps", label: "下一步想怎么做" }] as const;

export function NoticePage() {
  const client = useQueryClient();
  const accept = useMutation({ mutationFn: api.acceptNotice, onSuccess: me => client.setQueryData(ME_KEY, me) });
  return <div className="mx-auto max-w-xl p-6 pt-16"><Card className="p-7"><h1 className="mb-6 text-2xl font-semibold">开始前，了解这间工作台</h1><div className="space-y-4 leading-7"><p>这里用于研究协作、研究能力评估与研究助理改进。管理员及获授权的审核人可以查看相应的对话和提交内容，并提供反馈。</p><p>研究对话使用截止日期及之前的数据。测评先独立作答；提交后是否开放本题的后续核对数据，由管理员配置。</p><p>请勿提交与研究无关的个人敏感信息。</p></div>{accept.error && <div className="mt-4"><Notice>{errorText(accept.error)}</Notice></div>}<Button className="mt-7" variant="primary" disabled={accept.isPending} onClick={() => accept.mutate()}>了解并进入工作台</Button></Card></div>;
}

export function HomePage() {
  const assignments = useQuery({ queryKey: ["my-assignments"], queryFn: api.myAssignments, refetchOnMount: "always" });
  if (assignments.isPending) return <Spinner />;
  if (assignments.error) return <Page><Notice>{errorText(assignments.error)}</Notice></Page>;
  return <Navigate to={assignments.data.some(a => !a.completed) ? "/todo" : "/chat"} replace />;
}

export function TodoPage() {
  const assignments = useQuery({ queryKey: ["my-assignments"], queryFn: api.myAssignments, refetchInterval: 10_000 });
  return <Page><PageHeader title="我的待办" actions={<Link className="button-link" to="/chat">开始研究</Link>} />{assignments.isPending ? <Spinner /> : assignments.error ? <Notice>{errorText(assignments.error)}</Notice> : !assignments.data.length ? <Empty>还没有分配的题目。可以先开始研究对话。</Empty> : <div className="space-y-4">{[...assignments.data].sort((a, b) => Number(a.completed) - Number(b.completed)).map(a => <Card key={a.id} className="p-5"><div className="flex flex-wrap items-start justify-between gap-3"><h2 className="text-lg font-medium">{a.question.title}</h2><Badge tone={a.overdue ? "warn" : a.completed ? "ok" : "neutral"}>{a.completed ? "已提交" : a.overdue ? "已逾期" : "待完成"}</Badge></div><p className="mt-3 leading-7">{a.question.question}</p><div className="mt-5 flex flex-wrap items-center justify-between gap-3"><span className="text-sm text-muted">{a.due_at ? `截止 ${a.due_at}` : "无截止日期"}</span><Link className="button-link" to={a.attempt_id ? `/evaluate/${a.attempt_id}` : `/evaluate?assignment=${a.id}`}>{a.completed ? "查看答案" : "开始作答"}</Link></div></Card>)}</div>}</Page>;
}

export function IdeaContent({ idea, admin = false }: { idea: ResearchIdea; admin?: boolean }) {
  const me = useMe().data!;
  return <><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-medium">{idea.question}</h2><Badge tone={idea.status === "invited" ? "ok" : "neutral"}>{IDEA_LABELS[idea.status]}</Badge></div>{admin && <p className="mt-2 text-sm text-muted">{idea.display_name} · {dateTime(idea.created_at)}</p>}<dl className="mt-5 space-y-4">{idea.form_snapshot.filter(f => f.key !== "question").map(f => <div key={f.key}><dt className="text-sm text-muted">{f.label}</dt><dd className="mt-1 whitespace-pre-wrap leading-7">{idea[f.key] || "未填写"}</dd></div>)}</dl>{!!idea.linked_conversations.length && (!admin || can(me, "view_researchers")) && <div className="mt-4 flex flex-wrap gap-3">{idea.linked_conversations.map((id, i) => <Link key={id} className="text-sm underline" to={admin ? `/admin/researchers/${idea.user_id}?session=${id}` : `/chat/${id}`}>关联对话 {i + 1}</Link>)}</div>}{admin && idea.criteria_snapshot && <details className="mt-5 text-sm"><summary>提交时的审核标准</summary><p className="mt-3 whitespace-pre-wrap leading-7">{idea.criteria_snapshot}</p></details>}{idea.reviewer_feedback && <div className="mt-5"><Notice tone="neutral"><strong>审核反馈</strong><p className="whitespace-pre-wrap">{idea.reviewer_feedback}</p></Notice></div>}</>;
}

function IdeaForm({ onDone }: { onDone: () => void }) {
  const client = useQueryClient();
  const [form, setForm] = useState<IdeaInput>({ question: "", data_used: "", findings: "", next_steps: "", linked_conversations: [] });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions });
  const policy = useQuery({ queryKey: ["research-policy"], queryFn: api.researchPolicy });
  const create = useMutation({ mutationFn: () => api.createIdea(form), onSuccess: () => { client.invalidateQueries({ queryKey: ["my-ideas"] }); onDone(); } });
  if (policy.isPending) return <Spinner />;
  if (policy.error) return <Notice>{errorText(policy.error)}</Notice>;
  return <Card className="p-5"><form onSubmit={e => { e.preventDefault(); create.mutate(); }} className="space-y-5">{policy.data.invitation_criteria && <details><summary className="cursor-pointer font-medium">研究想法审核标准</summary><p className="mt-3 whitespace-pre-wrap leading-7">{policy.data.invitation_criteria}</p></details>}{policy.data.idea_fields.map(f => <Field key={f.key} label={`${f.label}${f.required ? "" : "（选填）"}`} hint={f.hint}><Textarea required={f.required} maxLength={4000} rows={f.key === "question" ? 2 : 3} value={form[f.key]} onChange={e => setForm({ ...form, [f.key]: e.target.value })} /></Field>)}<fieldset><legend className="mb-3 text-sm font-medium">关联我的对话（选填，最多 20 条）</legend>{sessions.isPending ? <Spinner /> : sessions.error ? <Notice>{errorText(sessions.error)}</Notice> : <div className="max-h-48 overflow-auto space-y-3">{!sessions.data.length ? <p className="text-sm text-muted">还没有研究对话。</p> : sessions.data.map(s => <label key={s.id} className="flex items-start gap-2 text-sm"><input type="checkbox" checked={form.linked_conversations.includes(s.id)} disabled={!form.linked_conversations.includes(s.id) && form.linked_conversations.length >= 20} onChange={e => setForm({ ...form, linked_conversations: e.target.checked ? [...form.linked_conversations, s.id] : form.linked_conversations.filter(id => id !== s.id) })} />{s.title}</label>)}</div>}</fieldset>{create.error && <Notice>{errorText(create.error)}</Notice>}<div className="flex justify-end gap-3"><Button onClick={onDone}>取消</Button><Button type="submit" variant="primary" disabled={create.isPending || policy.data.idea_fields.some(f => f.required && !form[f.key].trim())}>提交研究想法</Button></div></form></Card>;
}

export function MinePage() {
  const [tab, setTab] = useState("ideas");
  const [creating, setCreating] = useState(false);
  const ideas = useQuery({ queryKey: ["my-ideas"], queryFn: api.myIdeas });
  const issues = useQuery({ queryKey: ["my-feedback"], queryFn: api.myFeedback, enabled: tab === "feedback" });
  const attempts = useQuery({ queryKey: ["my-attempts"], queryFn: api.myAttempts, enabled: tab === "attempts" });
  return <Page><PageHeader title="我的研究" actions={<Button variant="primary" onClick={() => { setTab("ideas"); setCreating(true); }}>提交研究想法</Button>} /><div className="mb-6 flex gap-3" role="tablist">{[["ideas", "研究想法"], ["feedback", "问题反馈"], ["attempts", "作答记录"]].map(([key, label]) => <Button key={key} role="tab" aria-selected={tab === key} variant={tab === key ? "primary" : "ghost"} onClick={() => setTab(key)}>{label}</Button>)}</div><div className="space-y-4">{tab === "ideas" && <>{creating && <IdeaForm onDone={() => setCreating(false)} />}{ideas.isPending ? <Spinner /> : ideas.error ? <Notice>{errorText(ideas.error)}</Notice> : !ideas.data.length && !creating ? <Empty>把一个值得继续探索的问题，整理成你的第一份研究想法。</Empty> : ideas.data.map(idea => <Card key={idea.id} className="p-5"><IdeaContent idea={idea} /></Card>)}</>}{tab === "feedback" && (issues.isPending ? <Spinner /> : issues.error ? <Notice>{errorText(issues.error)}</Notice> : !issues.data.length ? <Empty>在回答旁或测评评分页提交问题反馈后，可在这里查看复核结果。</Empty> : issues.data.map(issue => <Card key={issue.id} className="p-5"><div className="flex justify-between gap-3"><Badge>{STAGES[issue.stage]}</Badge><Badge tone={issue.review_status === "confirmed" ? "ok" : "neutral"}>{REVIEW_LABELS[issue.review_status]}</Badge></div><p className="mt-4 whitespace-pre-wrap leading-7">{issue.description}</p><p className="mt-3 text-sm text-muted">{issue.question}</p>{issue.review_note && <div className="mt-4"><Notice tone="neutral">{issue.review_note}</Notice></div>}<details className="mt-4 text-sm"><summary>查看提交时的依据</summary><div className="mt-3"><EvidenceList evidence={issue.evidence_snapshot} /></div></details></Card>))}{tab === "attempts" && (attempts.isPending ? <Spinner /> : attempts.error ? <Notice>{errorText(attempts.error)}</Notice> : !attempts.data.length ? <Empty>还没有作答记录。</Empty> : attempts.data.map(a => <Card key={a.id} className="p-5"><div className="flex items-center justify-between gap-4"><p>{a.turn.question}</p><Link className="button-link shrink-0" to={`/evaluate/${a.id}`}>{a.rated ? "回看测评" : a.turn.status === "error" ? "查看失败记录" : "继续对照评分"}</Link></div></Card>))}</div></Page>;
}
