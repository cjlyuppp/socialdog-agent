import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { EvalAttempt, EvalCategory, VersionedQuestion, IssueDraft, NumberVerdict, RatingScores } from "../api/types";
import { FeedbackButton, IssueListEditor } from "../components/Feedback";
import { EFFORT_LABELS, ModelPicker, useModelSelection } from "../components/ModelPicker";
import type { ReasoningEffort } from "../api/types";
import { EvidenceList, GroundingWarning, JobProgress, Markdown } from "../components/answer";
import { Page } from "../components/Layout";
import { Icon } from "../components/Icon";
import { ReferenceExplorer } from "../components/ReferenceExplorer";
import { Badge, Button, Card, Empty, Field, Notice, PageHeader, Spinner, Textarea } from "../components/ui";
import { ME_KEY, useMe } from "../lib/auth";
import { errorText } from "../lib/format";
import { useJob } from "../lib/useJob";

export const CATEGORY_LABELS: Record<EvalCategory, string> = {
  time_series: "时间序列", product: "商品", price_event: "调价事件", quality: "数据质量",
  unavailable: "不能回答的问题", out_of_sample: "样本外",
};

const VERDICTS: { value: NumberVerdict; label: string }[] = [
  { value: "correct", label: "全部正确" }, { value: "partial", label: "部分正确" },
  { value: "wrong", label: "有错误" }, { value: "not_applicable", label: "不涉及数字" },
];

const SCALES: { key: "reasoning" | "definitions" | "limitations"; label: string; hint: string }[] = [
  { key: "reasoning", label: "推理过程", hint: "选的工具和步骤是否合理，结论是否由数据支撑" },
  { key: "definitions", label: "口径说明", hint: "是否说清样本、金额口径、时间范围" },
  { key: "limitations", label: "局限性说明", hint: "是否指出不能下的结论，例如因果、利润、隐藏分组" },
];

function QuestionPicker() {
  const questions = useQuery({ queryKey: ["eval-questions"], queryFn: api.evalQuestions });
  const [, setParams] = useSearchParams();
  if (questions.isPending) return <Spinner />;
  if (questions.error) return <Notice>{errorText(questions.error)}</Notice>;
  if (!questions.data.length) return <Empty>题目还在准备中。</Empty>;
  return (
    <div className="eval-question-list">
      {questions.data.map((q) => (
        <button key={q.id} type="button" onClick={() => setParams({ q: q.id })}
                className="eval-question">
          <div><h2>{q.title}</h2><p>{q.question}</p></div>
          <Badge tone={q.category === "out_of_sample" ? "accent" : "neutral"}>{CATEGORY_LABELS[q.category]}</Badge>
          <Icon name="right" size={18} />
        </button>
      ))}
    </div>
  );
}

function OwnAnswerStep({ question, assignmentId }: { question: VersionedQuestion; assignmentId?: string }) {
  const [answer, setAnswer] = useState("");
  const [, setParams] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const me = useMe().data!;
  const modelControls = useModelSelection();
  const run = useMutation({
    mutationFn: () => api.runEval(question.id, answer.trim(), question.bank_version_id, assignmentId, modelControls.selection),
    onSuccess: (attempt) => {
      client.invalidateQueries({ queryKey: ME_KEY });
      client.invalidateQueries({ queryKey: ["my-assignments"] });
      navigate(`/evaluate/${attempt.id}`);
    },
  });
  const blocked = me.quota.remaining_today <= 0 ? "今天的提问次数已用完，请明天再来。"
    : me.quota.site_remaining_today <= 0 ? "今日全站提问次数已用完，请明天再来。"
    : me.running_job_id ? "正在分析，完成后可以继续。" : null;

  return (
    <div className="space-y-5">
      <button type="button" className="text-sm text-muted hover:text-ink" onClick={() => setParams({})}>返回题目列表</button>
      <h2 className="text-xl font-medium leading-9">{question.question}</h2>
      {question.reference_hint && (
        <Card className="p-4">
          <div className="mb-4 text-sm font-medium">参考数据</div>
          <ReferenceExplorer initial={question.reference_hint} compact context={assignmentId ? { assignment_id: assignmentId } : { bank_version_id: question.bank_version_id }} />
        </Card>
      )}
      <Card className="p-5">
        <Field label="我的答案">
          <Textarea rows={6} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="写下你的结论、关键数字和理由…" />
        </Field>
        {(blocked || run.error) && <div className="mt-3"><Notice>{run.error ? errorText(run.error) : blocked}</Notice></div>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <ModelPicker controls={modelControls} disabled={run.isPending} />
          <Button variant="primary" disabled={!modelControls.selection || !answer.trim() || !!blocked || run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? "正在提交…" : "提交答案并对照"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function RatingForm({ attempt }: { attempt: EvalAttempt }) {
  const client = useQueryClient();
  const [scores, setScores] = useState<Partial<RatingScores>>({});
  const [otherScores, setOtherScores] = useState<Partial<RatingScores>>({});
  const [note, setNote] = useState("");
  const [issues, setIssues] = useState<IssueDraft[]>([]);
  const rate = useMutation({
    mutationFn: () => api.rate({ attempt_id: attempt.id, ...(attempt.anonymous_ab ? { anonymous_scores: { A: scores as RatingScores, B: otherScores as RatingScores } } : { scores: scores as RatingScores, issues }), note: note.trim() }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["attempt", attempt.id] });
      client.invalidateQueries({ queryKey: ["ratings"] });
      client.invalidateQueries({ queryKey: ["my-feedback"] });
    },
  });
  const isComplete = (values: Partial<RatingScores>) => values.numbers && SCALES.every(s => values[s.key]);
  const complete = isComplete(scores) && (!attempt.anonymous_ab || isComplete(otherScores));

  if (attempt.rated) {
    return (
      <Notice tone="ok">
        已提交打分，谢谢。<Link to="/ratings" className="underline">查看我的测评</Link> · <Link to="/evaluate" className="underline">再做一道</Link>
      </Notice>
    );
  }
  return (
    <Card className="p-5">
      <h2 className="mb-6 text-lg font-semibold">评价回答</h2>
      <ScoreFields scores={scores} setScores={setScores} prefix={attempt.anonymous_ab ? "A" : "model"} label={attempt.anonymous_ab ? "答案 A" : undefined} />
      {attempt.anonymous_ab && <ScoreFields scores={otherScores} setScores={setOtherScores} prefix="B" label="答案 B" />}
      <Field label="备注（可选）"><Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="哪里答错了、漏说了什么、口径哪里不清楚" /></Field>
      {!attempt.anonymous_ab && <IssueListEditor issues={issues} onChange={setIssues} evidence={attempt.turn.result?.evidence ?? []} />}
      {rate.error && <div className="mt-3"><Notice>{errorText(rate.error)}</Notice></div>}
      <div className="mt-4 flex justify-end">
        <Button variant="primary" disabled={!complete || issues.some(i => !i.description.trim()) || rate.isPending} onClick={() => rate.mutate()}>提交打分</Button>
      </div>
    </Card>
  );
}

function ScoreFields({ scores, setScores, prefix, label }: { scores: Partial<RatingScores>; setScores: (scores: Partial<RatingScores>) => void; prefix: string; label?: string }) {
  return <div className="mb-6 border-b border-line pb-3">{label && <h3 className="mb-5 font-semibold">{label}</h3>}
      <fieldset className="mb-5">
        <legend className="mb-3 text-sm font-medium">数字是否正确</legend>
        <div className="flex flex-wrap gap-2">
          {VERDICTS.map((v) => (
            <label key={v.value} className={`score-option cursor-pointer rounded-lg border px-3 py-2 text-sm ${scores.numbers === v.value ? "border-accent bg-accent-soft text-accent" : "border-line hover:bg-sidebar"}`}>
              <input type="radio" name={`${prefix}-numbers`} aria-label={label ? `${label} ${v.label}` : undefined} className="sr-only" checked={scores.numbers === v.value} onChange={() => setScores({ ...scores, numbers: v.value })} />
              {v.label}
            </label>
          ))}
        </div>
      </fieldset>
      {SCALES.map((scale) => (
        <fieldset key={scale.key} className="mb-5">
          <legend className="text-sm font-medium">{scale.label}</legend>
          <details className="my-2 text-xs text-muted"><summary className="underline">评分标准</summary><p className="mt-2 leading-6">{scale.hint}。1 分很差，5 分很好。</p></details>
          <div className="flex gap-1.5">
            {[1, 2, 3, 4, 5].map((n) => (
              <label key={n} className={`score-option grid h-10 w-10 cursor-pointer place-items-center rounded-lg border text-sm ${scores[scale.key] === n ? "border-accent bg-accent-soft text-accent" : "border-line hover:bg-sidebar"}`}>
                <input type="radio" name={`${prefix}-${scale.key}`} aria-label={`${label ? label + " " : ""}${scale.label} ${n} 分`} className="sr-only" checked={scores[scale.key] === n} onChange={() => setScores({ ...scores, [scale.key]: n })} />
                {n}
              </label>
            ))}
          </div>
        </fieldset>
      ))}
    </div>;
}

function AttemptView({ attemptId }: { attemptId: string }) {
  const client = useQueryClient();
  const policy = useQuery({ queryKey: ["research-policy"], queryFn: api.researchPolicy });
  const attempt = useQuery({ queryKey: ["attempt", attemptId], queryFn: () => api.attempt(attemptId) });
  const running = attempt.data?.turn.status === "running";
  const job = useJob(running ? attempt.data?.job_id : null);

  useEffect(() => {
    if (job.data?.status === "ok" || job.data?.status === "error") {
      client.invalidateQueries({ queryKey: ["attempt", attemptId] });
      client.invalidateQueries({ queryKey: ME_KEY });
    }
  }, [job.data?.status, client, attemptId]);

  if (attempt.isPending) return <Spinner />;
  if (attempt.error) return <Notice>{errorText(attempt.error)}</Notice>;
  const question = attempt.data.question_snapshot as unknown as VersionedQuestion | null;
  const { turn } = attempt.data;
  const blind = attempt.data.anonymous_ab && !attempt.data.rated;

  return (
    <div className="space-y-5">
      <Link to="/evaluate" className="text-xs text-muted hover:text-ink">← 返回题目列表</Link>
      <h2 className="text-xl font-medium leading-9">{turn.question}</h2>
      {attempt.data.anonymous_ab && attempt.data.rated && <Notice tone="neutral">{attempt.data.answer_panels.map(p => `答案 ${p.label}：${p.source === "agent" ? "研究助理" : "我的答案"}`).join("；")}</Notice>}
      {/* min-w-0: a wide answer table must scroll inside its card, not widen the grid. */}
      {blind ? <div className="grid gap-4 lg:grid-cols-2">{turn.status === "running" ? <JobProgress job={job.data} /> : turn.status === "error" ? <Notice>{turn.error}</Notice> : attempt.data.answer_panels.map(p => <Card className="min-w-0 p-5" key={p.label}><h3 className="mb-4 font-semibold">答案 {p.label}</h3><Markdown text={p.answer} /></Card>)}</div> : <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">我的答案</div>
          <p className="text-[15px] leading-7 whitespace-pre-wrap">{attempt.data.own_answer}</p>
        </Card>
        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">研究助理的回答</div>
          {turn.status === "running" && <JobProgress job={job.data} />}
          {turn.status === "error" && <Notice>{turn.error}</Notice>}
          {turn.result && (
            <>
              <Markdown text={turn.result.answer} />
              <GroundingWarning result={turn.result} />
              <details className="mt-4">
                <summary className="cursor-pointer text-sm text-muted underline">查看依据 · {turn.result.evidence.length}</summary>
                <div className="mt-3"><EvidenceList evidence={turn.result.evidence} /></div>
              </details>
              <div className="mt-4"><FeedbackButton turnId={turn.id} evidence={turn.result.evidence} attemptId={attempt.data.id} /></div>
              <p className="mt-3 text-sm text-muted">{turn.result.model} · 思考强度 {EFFORT_LABELS[turn.result.reasoning_effort as ReasoningEffort] ?? "未记录"}</p>
            </>
          )}
        </Card>
      </div>}
      {(question?.reference_hint || question?.post_cutoff_reference) && turn.status !== "running" && (
        <details className="rounded-xl border border-line bg-surface p-4">
          <summary className="cursor-pointer text-sm font-medium">核对参考数据</summary>
          <div className="mt-4"><ReferenceExplorer key={String(policy.data?.allow_post_cutoff_reference_after_submit)} initial={policy.data?.allow_post_cutoff_reference_after_submit && question.post_cutoff_reference ? { by: "day", ...question.post_cutoff_reference } : question.reference_hint} compact context={{ attempt_id: attempt.data.id }} /></div>
        </details>
      )}
      {turn.status === "ok" && <RatingForm attempt={attempt.data} />}
    </div>
  );
}

export function EvaluatePage() {
  const { attemptId } = useParams();
  const [params] = useSearchParams();
  const questions = useQuery({ queryKey: ["eval-questions"], queryFn: api.evalQuestions });
  const assignments = useQuery({ queryKey: ["my-assignments"], queryFn: api.myAssignments, enabled: !!params.get("assignment") });
  const assigned = assignments.data?.find(a => a.id === params.get("assignment"));
  const picked = assigned?.question ?? questions.data?.find((q) => q.id === params.get("q"));

  return (
    <Page wide={!!attemptId}>
      <PageHeader title="测评" />
      <ol className="eval-step" aria-label="测评步骤">
        {["选择题目", "独立作答", "对照评分"].map((step, index) => <li key={step} className={(attemptId ? 2 : picked ? 1 : 0) === index ? "is-active" : ""} aria-current={(attemptId ? 2 : picked ? 1 : 0) === index ? "step" : undefined}>{index + 1}. {step}</li>)}
      </ol>
      {attemptId ? <AttemptView attemptId={attemptId} /> : params.get("assignment") && assignments.isPending ? <Spinner /> : params.get("assignment") && !assigned ? <Notice>待办不存在或无法加载，请返回我的待办重试。</Notice> : assigned?.attempt_id ? <Link className="button-link" to={`/evaluate/${assigned.attempt_id}`}>查看已提交的答案</Link> : picked ? <OwnAnswerStep key={`${picked.bank_version_id}-${picked.id}`} question={picked} assignmentId={assigned?.id} /> : <QuestionPicker />}
    </Page>
  );
}
