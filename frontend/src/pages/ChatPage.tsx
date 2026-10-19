import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DataStatus, Turn } from "../api/types";
import { EvidenceList, GroundingWarning, JobProgress, Markdown } from "../components/answer";
import { Icon } from "../components/Icon";
import { Notice, Spinner } from "../components/ui";
import { ME_KEY, useMe } from "../lib/auth";
import { dateTime, errorText } from "../lib/format";
import { useJob } from "../lib/useJob";

const SUGGESTIONS = [
  { title: "了解数据范围", text: "历史数据覆盖什么时间？一共有多少订单？" },
  { title: "看看月度走势", text: "按月汇总基准样本的订单数和记录收款金额。" },
  { title: "分析调价前后", text: "历史上有没有商品调价？调价前后销量和收款怎么变？" },
];

function DataInfo({ status, error }: { status?: DataStatus; error: boolean }) {
  return (
    <details className="data-info">
      <summary><Icon name="data" size={16} /><span>{error ? "数据状态暂不可用" : status ? `数据截至 ${status.cutoff_date}` : "正在读取数据范围"}</span><Icon name="chevron" size={14} /></summary>
      <div className="data-info-content">
        <h2>可查询的数据</h2>
        <dl>
          <dt>时间范围</dt><dd>{status ? `${status.agent_observation_start} 至 ${status.agent_observation_end}` : "—"}</dd>
          <dt>分组最少订单数</dt><dd>{status?.min_orders_per_group ?? "—"}</dd>
          <dt>模型</dt><dd>{status?.model ?? "—"}</dd>
        </dl>
        <p>仅查询截止日期及之前的数据。不足最少订单数的分组不展示。</p>
        <details className="technical-details"><summary>数据版本</summary><p>{status?.dataset_version ?? "—"}</p></details>
      </div>
    </details>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const job = useJob(turn.status === "running" ? turn.job_id : null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(turn.result!.answer);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }

  return (
    <article className="conversation-turn">
      <div className="user-question">{turn.question}</div>
      <div className="assistant-answer">
        <div className="answer-avatar" aria-hidden="true">S·</div>
        <div className="answer-body">
          {turn.status === "running" && <JobProgress job={job.data} />}
          {turn.status === "error" && <Notice>{turn.error}</Notice>}
          {turn.status === "ok" && turn.result && <>
            <Markdown text={turn.result.answer} />
            <GroundingWarning result={turn.result} />
            <div className="answer-actions">
              <details className="answer-evidence">
                <summary><Icon name="data" size={16} />查看依据<span>{turn.result.evidence.length}</span></summary>
                <div className="evidence-content">
                  <EvidenceList evidence={turn.result.evidence} />
                  <details className="technical-details"><summary>回答信息</summary><dl><dt>时间</dt><dd>{dateTime(turn.created_at)}</dd><dt>模型</dt><dd>{turn.result.model}</dd><dt>记录编号</dt><dd>{turn.result.request_id}</dd></dl></details>
                </div>
              </details>
              <button type="button" className="copy-answer" onClick={copy}><Icon name={copyState === "copied" ? "check" : "copy"} size={16} />{copyState === "copied" ? "已复制" : copyState === "error" ? "复制失败，重试" : "复制"}</button>
            </div>
          </>}
        </div>
      </div>
    </article>
  );
}

export function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const me = useMe().data!;
  const [question, setQuestion] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const status = useQuery({ queryKey: ["status"], queryFn: api.status, staleTime: 60_000 });
  const session = useQuery({ queryKey: ["session", sessionId], queryFn: () => api.session(sessionId!), enabled: !!sessionId });
  const turns = session.data?.turns ?? [];
  const running = turns.find((t) => t.status === "running");
  const runningJob = useJob(running?.job_id);

  useEffect(() => {
    if (runningJob.data?.status === "ok" || runningJob.data?.status === "error") {
      client.invalidateQueries({ queryKey: ["session", sessionId] });
      client.invalidateQueries({ queryKey: ME_KEY });
      client.invalidateQueries({ queryKey: ["sessions"] });
    }
  }, [runningJob.data?.status, client, sessionId]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }, [turns.length, turns[turns.length - 1]?.status]);

  useEffect(() => { setQuestion(""); }, [sessionId]);

  const ask = useMutation({
    mutationFn: async (text: string) => {
      const id = sessionId ?? (await api.createSession()).id;
      await api.ask(id, text);
      return id;
    },
    onSuccess: (id) => {
      setQuestion("");
      client.invalidateQueries({ queryKey: ["session", id] });
      client.invalidateQueries({ queryKey: ["sessions"] });
      client.invalidateQueries({ queryKey: ME_KEY });
      if (id !== sessionId) navigate(`/chat/${id}`);
    },
  });

  const blocked = me.quota.remaining_today <= 0 ? "今天的提问次数已用完，请明天再来。"
    : me.quota.site_remaining_today <= 0 ? "今日全站提问次数已用完，请明天再来。"
    : running || me.running_job_id ? "正在分析，完成后可以继续提问。" : null;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const text = question.trim();
    if (text && !blocked && !ask.isPending) ask.mutate(text);
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  }

  const empty = !sessionId;
  return (
    <div className="chat-workspace">
      <header className="workspace-header"><h1>{session.data?.title ?? "研究对话"}</h1><DataInfo status={status.data} error={status.isError} /></header>
      <div className={`chat-stage ${empty ? "is-empty" : ""}`}>
        {empty ? <div className="chat-welcome"><h2>今天想研究什么？</h2></div> : (
          <div ref={scroller} className="conversation-scroll">
            <div className="conversation-content">
              {session.isPending ? <Spinner label="正在加载对话" /> : session.error ? <Notice>{errorText(session.error)}</Notice> : turns.length ? turns.map((turn) => <TurnView key={turn.id} turn={turn} />) : <div className="empty-session">开始一个新问题</div>}
            </div>
          </div>
        )}
        <div className="composer-area">
          {(ask.error || (blocked && !running)) && <div className="mb-3"><Notice>{ask.error ? errorText(ask.error) : blocked}</Notice></div>}
          <form onSubmit={submit} className="question-composer">
            <label htmlFor="question" className="sr-only">输入研究问题</label>
            <textarea ref={input} id="question" rows={2} maxLength={4000} value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={onKey} placeholder="向历史数据提问…" />
            <div className="composer-toolbar"><button type="submit" aria-label="发送问题" disabled={!question.trim() || !!blocked || ask.isPending}><Icon name="arrow" size={20} /></button></div>
          </form>
          {empty && <div className="question-suggestions" aria-label="试试这些问题">{SUGGESTIONS.map((suggestion) => <button key={suggestion.title} type="button" onClick={() => { setQuestion(suggestion.text); input.current?.focus(); }}>{suggestion.title}<Icon name="right" size={15} /></button>)}</div>}
        </div>
      </div>
    </div>
  );
}
