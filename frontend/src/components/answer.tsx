// Rendering of agent answers, their evidence and live job progress.
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentResult, Evidence, Job } from "../api/types";
import { Badge, Empty } from "./ui";
import { Icon } from "./Icon";

const TOOL_LABELS: Record<string, string> = {
  history_describe: "数据描述", history_aggregate: "历史聚合", history_price_events: "调价事件",
  history_quality: "数据质量", history_unavailable: "不可开展的分析",
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // Model output is untrusted: no raw HTML, and links never navigate the app.
        skipHtml
        components={{
          table: ({ children }) => <div className="table-wrap"><table>{children}</table></div>,
          a: ({ children }) => <span className="underline decoration-dotted">{children}</span>,
          img: ({ alt }) => alt ? <span>{alt}</span> : null,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function GroundingWarning({ result }: { result: AgentResult }) {
  const numbers = result.grounding.ungrounded_numbers;
  if (!numbers.length) return null;
  return (
    <div role="status" className="mt-4 rounded-lg border border-[#ecdacb] bg-warn-soft px-4 py-3 text-sm leading-7 text-warn">
      以下数字尚未核实，请核对后使用：{numbers.join("、")}
    </div>
  );
}

function prettyArguments(raw: string) {
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const entries = Object.entries(parsed).filter(([, v]) => v !== null);
    return entries.length ? entries.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join("  ") : "无参数";
  } catch {
    return raw;
  }
}

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return <Empty>这条回答没有查询记录。</Empty>;
  return (
    <div className="space-y-2">
      {evidence.map((item, index) => (
        <details key={item.call_id + index} className="group overflow-hidden rounded-lg border border-[#e4e8da] bg-surface">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm text-ink">
            <Icon name="chevron" size={15} className="transition-transform group-open:rotate-90" />
            <span className="font-medium">{index + 1}. {TOOL_LABELS[item.tool] ?? item.tool}</span>
            <span className="ml-auto"><Badge tone={item.result.status === "ok" ? "ok" : "warn"}>{item.result.status === "ok" ? "已查询" : item.result.status === "unavailable" ? "数据不足" : "查询异常"}</Badge></span>
          </summary>
          <div className="px-4 pb-2 font-mono text-xs leading-6 text-muted break-all">{item.tool} · {prettyArguments(item.arguments)}</div>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all px-4 pb-4 text-xs leading-6 text-muted">
            {JSON.stringify(item.result, null, 2)}
          </pre>
        </details>
      ))}
    </div>
  );
}

export function JobProgress({ job }: { job: Job | undefined }) {
  const events = job?.events ?? [];
  const latest = events[events.length - 1];
  const labels = { queued: "正在排队", source: "正在读取数据", model: "正在分析", tool: "正在查询数据", tool_complete: "数据已就绪", verify: "正在核对数字", complete: "分析完成" };
  return (
    <div className="text-sm leading-7 text-muted" aria-live="polite">
      <span className="mr-2 inline-block h-2 w-2 rounded-full bg-accent animate-pulse" />
      {job?.status === "queued" && job.queue_position ? `排队中，当前位置 ${job.queue_position}` : latest ? labels[latest.phase] : "正在提交"}
      {events.length > 1 && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer underline">查看进度</summary>
          <ol className="mt-2 space-y-1">
          {events.map((event, i) => (
            <li key={i}>{event.text}</li>
          ))}
          </ol>
        </details>
      )}
    </div>
  );
}
