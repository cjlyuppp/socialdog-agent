import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FeedbackIssue, IdeaStatus, ResearchIdea, ReviewStatus } from "../api/types";
import { EvidenceList, Markdown } from "../components/answer";
import { REVIEW_LABELS, STAGES } from "../components/Feedback";
import { Page } from "../components/Layout";
import { Badge, Button, Card, Empty, Field, Input, Notice, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { dateTime, errorText } from "../lib/format";
import { can, useMe } from "../lib/auth";
import { IDEA_LABELS, IdeaContent } from "./ResearchPages";
import { RatingCard } from "./RatingsPage";

function IssueReviewCard({ issue }: { issue: FeedbackIssue }) {
  const mayInspect = can(useMe().data!, "view_researchers");
  const client = useQueryClient();
  const [status, setStatus] = useState(issue.review_status);
  const [note, setNote] = useState(issue.review_note);
  const review = useMutation({ mutationFn: () => api.reviewIssue(issue.id, status, note), onSuccess: () => { client.invalidateQueries({ queryKey: ["feedback-queue"] }); client.invalidateQueries({ queryKey: ["researcher"] }); } });
  return <Card className="p-5 review-context"><div className="flex flex-wrap justify-between items-center gap-3">{mayInspect ? <Link className="font-medium underline" to={`/admin/researchers/${issue.user_id}`}>{issue.display_name}</Link> : <span className="font-medium">{issue.display_name}</span>}<div className="flex gap-2"><Badge>{issue.source === "chat" ? "研究对话" : "测评"}</Badge><Badge>{STAGES[issue.stage]}</Badge><Badge tone={issue.review_status === "confirmed" ? "ok" : "neutral"}>{REVIEW_LABELS[issue.review_status]}</Badge></div></div><p className="mt-5 whitespace-pre-wrap leading-7">{issue.description}</p>{issue.correct_approach && <p className="mt-4 whitespace-pre-wrap leading-7"><strong>建议做法：</strong>{issue.correct_approach}</p>}<details className="mt-5 rounded-lg bg-sidebar p-4"><summary className="cursor-pointer font-medium">原问题、模型回答与依据</summary><div className="mt-4 space-y-5"><p className="font-medium">{issue.question}</p><Markdown text={issue.model_answer} />{!!issue.evidence_refs.length && <p className="text-sm text-muted">已关联依据：{issue.evidence_snapshot.map((e, i) => issue.evidence_refs.includes(e.call_id) ? i + 1 : null).filter(Boolean).join("、")}</p>}<EvidenceList evidence={issue.evidence_snapshot} /></div></details><form className="mt-5 space-y-4" onSubmit={e => { e.preventDefault(); review.mutate(); }}><Field label="复核结果"><Select value={status} onValueChange={e => setStatus(e as ReviewStatus)}>{Object.entries(REVIEW_LABELS).map(([v, label]) => <option value={v} key={v}>{label}</option>)}</Select></Field><Field label="复核备注"><Textarea maxLength={4000} rows={2} value={note} onChange={e => setNote(e.target.value)} /></Field><div className="flex items-center gap-3"><Button type="submit" variant="primary" disabled={review.isPending}>保存复核</Button>{review.isSuccess && <span className="text-sm text-ok">已保存</span>}{review.error && <Notice>{errorText(review.error)}</Notice>}</div></form></Card>;
}

export function FeedbackQueue() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const users = useQuery({ queryKey: ["review-members"], queryFn: api.reviewMembers });
  const issues = useQuery({ queryKey: ["feedback-queue", filters], queryFn: () => api.feedbackQueue(filters) });
  const fields = [{ key: "source", label: "来源", options: { chat: "研究对话", evaluation: "测评" } }, { key: "stage", label: "环节", options: STAGES }, { key: "review_status", label: "状态", options: REVIEW_LABELS }, { key: "user_id", label: "研究员", options: Object.fromEntries((users.data ?? []).map(u => [u.id, u.display_name])) }];
  return <div className="space-y-5"><div className="flex flex-wrap justify-between gap-3"><p className="text-muted">问题反馈复核</p><a className="button-link" href="/api/admin/improvements/export.json" download>导出模型改进清单</a></div><div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{fields.map(f => <Field label={f.label} key={f.key}><Select value={filters[f.key] ?? ""} onValueChange={e => setFilters({ ...filters, [f.key]: e })}><option value="">全部</option>{Object.entries(f.options).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</Select></Field>)}</div>{users.error && <Notice>{errorText(users.error)}</Notice>}{issues.isPending ? <Spinner /> : issues.error ? <Notice>{errorText(issues.error)}</Notice> : !issues.data.length ? <Empty>没有符合条件的问题反馈。</Empty> : issues.data.map(i => <IssueReviewCard key={i.id} issue={i} />)}</div>;
}

function IdeaReviewCard({ idea }: { idea: ResearchIdea }) {
  const client = useQueryClient();
  const [status, setStatus] = useState(idea.status);
  const [feedback, setFeedback] = useState(idea.reviewer_feedback);
  const review = useMutation({ mutationFn: () => api.reviewIdea(idea.id, status, feedback), onSuccess: () => { client.invalidateQueries({ queryKey: ["all-ideas"] }); client.invalidateQueries({ queryKey: ["researcher"] }); } });
  return <Card className="p-5"><IdeaContent idea={idea} admin /><form onSubmit={e => { e.preventDefault(); review.mutate(); }} className="mt-6 space-y-4"><Field label="审核状态"><Select value={status} onValueChange={e => setStatus(e as IdeaStatus)}>{Object.entries(IDEA_LABELS).map(([v, label]) => <option value={v} key={v}>{label}</option>)}</Select></Field><Field label="给研究员的反馈"><Textarea maxLength={4000} rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} /></Field><div className="flex items-center gap-3"><Button type="submit" variant="primary" disabled={review.isPending}>保存审核</Button>{review.isSuccess && <span className="text-sm text-ok">已保存</span>}{review.error && <Notice>{errorText(review.error)}</Notice>}</div></form></Card>;
}

export function IdeasQueue() {
  const ideas = useQuery({ queryKey: ["all-ideas"], queryFn: api.allIdeas });
  return <div className="space-y-4">{ideas.isPending ? <Spinner /> : ideas.error ? <Notice>{errorText(ideas.error)}</Notice> : !ideas.data.length ? <Empty>还没有提交的研究想法。</Empty> : ideas.data.map(i => <IdeaReviewCard key={i.id} idea={i} />)}</div>;
}

export function AssignmentsTab() {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: api.users });
  const questions = useQuery({ queryKey: ["eval-questions"], queryFn: api.evalQuestions });
  const progress = useQuery({ queryKey: ["admin-progress"], queryFn: api.progress, refetchInterval: 10_000 });
  const assignments = useQuery({ queryKey: ["admin-assignments"], queryFn: api.assignments, refetchInterval: 10_000 });
  const versions = useQuery({ queryKey: ["bank-versions"], queryFn: api.bankVersions });
  const [userId, setUserId] = useState("");
  const [questionId, setQuestionId] = useState("");
  const [due, setDue] = useState("");
  const selected = questions.data?.find(q => q.id === questionId);
  const assign = useMutation({ mutationFn: () => api.assign({ user_id: userId, question_id: questionId, bank_version_id: selected!.bank_version_id, due_at: due || null }), onSuccess: () => { client.invalidateQueries({ queryKey: ["admin-progress"] }); client.invalidateQueries({ queryKey: ["admin-assignments"] }); client.invalidateQueries({ queryKey: ["my-assignments"] }); } });
  const error = users.error ?? questions.error ?? progress.error ?? assignments.error ?? versions.error;
  return <div className="space-y-5">{error && <Notice>{errorText(error)}</Notice>}<Card className="p-5"><form onSubmit={e => { e.preventDefault(); assign.mutate(); }} className="grid items-end gap-4 md:grid-cols-4"><Field label="研究员"><Select required value={userId} onValueChange={e => setUserId(e)}><option value="">选择研究员</option>{users.data?.filter(u => u.role === "researcher" && !u.disabled).map(u => <option value={u.id} key={u.id}>{u.display_name}</option>)}</Select></Field><Field label="题目"><Select required value={questionId} onValueChange={e => setQuestionId(e)}><option value="">选择题目</option>{questions.data?.map(q => <option value={q.id} key={q.id}>{q.title}</option>)}</Select></Field><Field label="截止日期（选填）"><Input type="date" value={due} onChange={e => setDue(e.target.value)} /></Field><Button type="submit" variant="primary" disabled={!selected || !userId || assign.isPending}>分配题目</Button></form>{questions.data?.length === 0 && <p className="mt-4 text-sm text-muted">尚未导入正式题库。</p>}{assign.error && <div className="mt-4"><Notice>{errorText(assign.error)}</Notice></div>}{assign.isSuccess && <p className="mt-4 text-sm text-ok">已分配，研究员可在“我的待办”看到。</p>}</Card><Card className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["研究员", "已分配", "已提交", "逾期未交"].map(h => <th className="p-4 font-medium" key={h}>{h}</th>)}</tr></thead><tbody>{progress.data?.map(p => <tr key={p.user_id} className="border-t border-line"><td className="p-4"><Link className="underline" to={`/admin/researchers/${p.user_id}`}>{p.display_name}</Link></td><td className="p-4">{p.assigned}</td><td className="p-4">{p.completed}</td><td className="p-4">{p.overdue}</td></tr>)}</tbody></table></Card>{assignments.data?.map(a => <Card key={a.id} className="p-4 flex flex-wrap justify-between gap-3"><span>{a.display_name} · {a.question.title}</span><span className="text-sm text-muted">{a.due_at ?? "无截止日期"} · {a.completed ? "已提交" : a.overdue ? "逾期未交" : "待完成"}</span></Card>)}<details className="rounded-xl border border-line p-5"><summary className="cursor-pointer font-medium">题库版本</summary><div className="mt-4 space-y-3">{versions.data?.map(v => <div key={v.id} className="text-sm"><code>{v.id.slice(0, 10)}</code> · T {v.cutoff_T ?? "历史未知"} · N {v.threshold_N ?? "历史未知"} · {v.question_count} 题{v.legacy ? " · 历史记录" : ""}</div>)}</div></details></div>;
}

function ConversationReview({ userId, sessionId }: { userId: string; sessionId: string }) {
  const session = useQuery({ queryKey: ["inspect-session", userId, sessionId], queryFn: () => api.inspectSession(userId, sessionId) });
  if (session.isPending) return <Spinner />;
  if (session.error) return <Notice>{errorText(session.error)}</Notice>;
  return <Card className="p-5 review-context"><h2 className="mb-5 font-semibold">{session.data.title}</h2><div className="space-y-7">{session.data.turns.map(t => <article key={t.id}><p className="mb-4 font-medium">{t.question}</p>{t.result ? <><Markdown text={t.result.answer} /><details className="mt-4"><summary>查看依据</summary><div className="mt-3"><EvidenceList evidence={t.result.evidence} /></div></details></> : <p>{t.error ?? "正在分析"}</p>}</article>)}</div></Card>;
}

export function ResearcherPage() {
  const { userId } = useParams();
  const [params] = useSearchParams();
  const data = useQuery({ queryKey: ["researcher", userId], queryFn: () => api.researcher(userId!) });
  if (data.isPending) return <Spinner />;
  if (data.error) return <Page><Notice>{errorText(data.error)}</Notice></Page>;
  const s = data.data;
  return <Page wide><PageHeader title={s.user.display_name} actions={<Link className="button-link" to="/admin?tab=users">返回成员</Link>} /><div className="space-y-6"><div className="grid gap-4 sm:grid-cols-3"><Card className="p-5"><p className="text-sm text-muted">研究提问</p><p className="mt-2 text-2xl">{s.question_count}</p></Card><Card className="p-5"><p className="text-sm text-muted">问题反馈 / 成立</p><p className="mt-2 text-2xl">{s.feedback_count} / {s.confirmed_count}</p><p className="mt-3 text-sm text-muted">已判定 {s.reviewed_count} 条，成立比例 {s.confirmed_ratio === null ? "—" : `${Math.round(s.confirmed_ratio * 100)}%`}</p></Card><Card className="p-5"><p className="text-sm text-muted">今日额度</p><p className="mt-2 text-2xl">{s.quota.used_today} / {s.quota.daily_limit}</p><p className="mt-3 text-sm text-muted">测评预留 {s.quota.evaluation_reserve} 次 · 对话剩余 {s.quota.chat_remaining_today} 次</p></Card></div><Card className="p-5"><h2 className="mb-4 text-lg font-medium">最近提问</h2>{!s.recent_questions.length ? <Empty>还没有研究提问。</Empty> : <div className="divide-y divide-line">{s.recent_questions.map(q => <Link className="block py-3" key={q.id} to={`?session=${q.session_id}`}><p className="underline">{q.question}</p><time className="mt-2 block text-sm text-muted">{dateTime(q.created_at)}</time></Link>)}</div>}</Card>{params.get("session") && <ConversationReview userId={userId!} sessionId={params.get("session")!} />}<section><h2 className="mb-4 text-lg font-medium">对研究助理的测评评分</h2><div className="space-y-4">{!s.ratings.length ? <Empty>还没有完成评分。</Empty> : s.ratings.map(r => <RatingCard key={r.id} rating={r} />)}</div></section><section><h2 className="mb-4 text-lg font-medium">研究想法</h2><div className="space-y-4">{!s.ideas.length ? <Empty>还没有提交研究想法。</Empty> : s.ideas.map(i => <Card className="p-5" key={i.id}><IdeaContent idea={i} admin /></Card>)}</div></section></div></Page>;
}
