// In-browser state for the mock backend, persisted to localStorage so reloads keep sessions.
import type {
  AdminUser, Assignment, BankWorkspace, EvalAttempt, EvalQuestion, FeedbackIssue, Job, ModelSelection, ModelServiceSettings, ProgressEvent, Rating, ResearchIdea, Session, SiteSettings, Turn,
} from "../api/types";
import { CUTOFF } from "./data";
import { runMockAgent } from "./agent";

export interface UserRecord extends Omit<AdminUser, "used_today"> {
  notice_version: number;
  password: string | null;
  invite_token: string | null;
  invite_expires_at: string | null;
}

interface JobRecord {
  id: string;
  user_id: string;
  session_id: string;
  turn_id: string;
  question: string;
  started_ms: number;
  selection?: ModelSelection;
}

interface UsageRecord {
  user_id: string;
  date: string;
  input_tokens: number;
  output_tokens: number;
}

export interface State {
  users: UserRecord[];
  current_user_id: string | null;
  csrf: string;
  sessions: (Session & { user_id: string })[];
  turns: (Turn & { session_id: string })[];
  jobs: JobRecord[];
  attempts: (Omit<EvalAttempt, "turn" | "rated"> & { user_id: string; turn_id: string; answer_order?: string[] })[];
  ratings: (Rating & { user_id: string })[];
  feedback: FeedbackIssue[];
  ideas: ResearchIdea[];
  assignments: Assignment[];
  usage: UsageRecord[];
  settings: SiteSettings;
  bank: BankWorkspace | null;
  modelService: ModelServiceSettings;
}

const KEY = "socialdog-mock-v2";
export const id = () => crypto.randomUUID().replaceAll("-", "");
export const now = () => new Date().toISOString();
export const today = () => new Date().toISOString().slice(0, 10);

function seed(): State {
  const user = (fields: Partial<UserRecord> & Pick<UserRecord, "username" | "display_name" | "role">): UserRecord => ({
    id: id(), daily_limit: 30, evaluation_reserve: 0, notice_version: 0, permissions: [], disabled: false, last_active_at: null, invite_pending: false,
    password: "demo", invite_token: null, invite_expires_at: null, ...fields,
  });
  const users = [
    user({ username: "carrey", display_name: "Carrey", role: "admin", daily_limit: 100 }),
    user({ username: "a", display_name: "A", role: "admin", daily_limit: 100 }),
    user({ username: "lin", display_name: "林研究员", role: "researcher" }),
    user({ username: "zhou", display_name: "周研究员", role: "researcher", password: null, invite_pending: true,
           invite_token: "demo-invite", invite_expires_at: new Date(Date.now() + 3 * 86400_000).toISOString() }),
  ];
  // A month of historical usage so the admin report has something to show.
  const usage: UsageRecord[] = [];
  for (let d = 1; d <= 18; d++) {
    const date = new Date(Date.now() - d * 86400_000).toISOString().slice(0, 10);
    users.slice(0, 3).forEach((u, i) => {
      for (let k = 0; k < (d + i) % 5; k++) usage.push({ user_id: u.id, date, input_tokens: 6100 + 400 * k, output_tokens: 820 + 90 * i });
    });
  }
  return {
    users, current_user_id: null, csrf: id(), sessions: [], turns: [], jobs: [], attempts: [], ratings: [], feedback: [], ideas: [], assignments: [], usage, bank: null,
    modelService: { provider: "deepseek", configured: true, updated_at: null },
    settings: {
      site_daily_limit: 200, min_orders_per_group: 5, cutoff_date: CUTOFF,
      input_price_per_million_yuan: 2, output_price_per_million_yuan: 8,
      max_concurrent_jobs: 3, per_user_concurrent_jobs: 1,
      allow_post_cutoff_reference_after_submit: false,
      anonymous_ab_evaluation: false,
      max_accounts: 10, default_daily_limit: 30, default_evaluation_reserve: 0, invitation_criteria: "",
      idea_fields: [{ key: "question", label: "研究问题" }, { key: "data_used", label: "用到的数据" }, { key: "findings", label: "初步发现" }, { key: "next_steps", label: "下一步想怎么做" }].map(f => ({ ...f, key: f.key as "question", hint: "", required: true })),
    },
  };
}

function load(): State {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved) {
      const previous = JSON.parse(saved) as State;
      const defaults = seed();
      return { ...defaults, ...previous, settings: { ...defaults.settings, ...previous.settings },
        users: previous.users.map(u => ({ ...u, permissions: u.permissions ?? [] })),
        ideas: (previous.ideas ?? []).map(i => ({ ...i, form_snapshot: i.form_snapshot ?? defaults.settings.idea_fields, criteria_snapshot: i.criteria_snapshot ?? "" })),
        attempts: previous.attempts.map(a => ({ ...a, anonymous_ab: a.anonymous_ab ?? false, answer_panels: a.answer_panels ?? [] })) };
    }
  } catch {
    // Private mode or blocked storage: fall back to a fresh in-memory state.
  }
  return seed();
}

export const db: State = load();

export function save() {
  try {
    localStorage.setItem(KEY, JSON.stringify(db));
  } catch {
    // Persistence is a convenience only.
  }
}

export function resetMock() {
  Object.assign(db, seed());
  save();
}

export function usedToday(userId: string) {
  return db.usage.filter((u) => u.user_id === userId && u.date === today()).length;
}

// ---- Jobs advance by elapsed time, mimicking the server's progress events.

const SCHEDULE: [number, ProgressEvent["phase"], string][] = [
  [0, "queued", "已进入队列"], [500, "source", "正在锁定历史数据版本"], [1100, "model", "正在选择研究工具"],
  [1900, "tool", "正在运行研究工具"], [2700, "tool_complete", "研究工具已返回"], [3300, "model", "正在整理回答"],
  [4000, "verify", "正在核对回答中的数字"], [4600, "complete", "回答与工具依据已保存"],
];
const DURATION = SCHEDULE[SCHEDULE.length - 1][0];

export function advance(job: JobRecord): Job {
  const turn = db.turns.find((t) => t.id === job.turn_id)!;
  const elapsed = Date.now() - job.started_ms;
  const events = SCHEDULE.filter(([at]) => elapsed >= at).map(([at, phase, text]) => ({
    phase, text, at: new Date(job.started_ms + at).toISOString(),
    ...(phase === "tool" ? { tool: "history_aggregate" } : {}),
  }));
  if (turn.status === "running" && elapsed >= DURATION) {
    if (/失败/.test(job.question)) {
      Object.assign(turn, { status: "error", error: "模拟错误：模型请求超时，请重试或缩小问题。" });
    } else {
      const result = runMockAgent(job.question);
      result.model = (job.selection?.model ?? "deepseek-flash") + "（模拟）";
      result.reasoning_effort = job.selection?.reasoning_effort ?? "high";
      Object.assign(turn, { status: "ok", result });
      db.usage.push({ user_id: job.user_id, date: today(), ...result.usage });
    }
    save();
  }
  const status = turn.status === "running" ? (elapsed < SCHEDULE[1][0] ? "queued" : "running") : turn.status;
  return {
    id: job.id, status, session_id: job.session_id, turn_id: job.turn_id,
    queue_position: status === "queued" ? 1 : null, events, error: turn.error,
  };
}

export function startJob(userId: string, sessionId: string, question: string, selection?: ModelSelection): { turn: Turn; job: Job } {
  const turn = { id: id(), session_id: sessionId, question, status: "running" as const, created_at: now(),
                 job_id: id(), result: null, error: null };
  const job = { id: turn.job_id, user_id: userId, session_id: sessionId, turn_id: turn.id, question, started_ms: Date.now(), selection };
  db.turns.push(turn);
  db.jobs.push(job);
  save();
  return { turn, job: advance(job) };
}

export function runningJob(userId: string): JobRecord | undefined {
  return db.jobs.find((j) => j.user_id === userId && db.turns.find((t) => t.id === j.turn_id)?.status === "running");
}

export function settle() {
  // Finish any job whose time has passed, so lists show final states.
  for (const job of db.jobs) advance(job);
}

export const QUESTIONS: EvalQuestion[] = ([
  { id: "q-month", category: "time_series", title: "月度订单走势", question: "按月汇总基准样本的订单数和记录收款金额，哪个月最高？",
    reference_hint: { scope: "baseline", by: "month", end_date: CUTOFF } },
  { id: "q-week", category: "time_series", title: "寒假前后的周订单", question: "按周列出 2023 年 1–3 月基准样本的订单数，寒假前后有什么变化？",
    reference_hint: { scope: "baseline", by: "week", start_date: "2023-01-01", end_date: "2023-03-31" } },
  { id: "q-product", category: "product", title: "商品销量结构", question: "截至目前，各商品的记录件数和记录收款金额分别是多少？",
    reference_hint: { scope: "baseline", by: "product", end_date: CUTOFF } },
  { id: "q-price", category: "price_event", title: "拿铁降价前后", question: "历史上有没有商品降价？降价后销量和收款有没有提升？利润呢？",
    reference_hint: { scope: "baseline", by: "day", product: "拿铁", start_date: "2023-02-15", end_date: "2023-03-14" } },
  { id: "q-weekday", category: "time_series", title: "星期规律", question: "按星期看基准样本的订单分布，周末和工作日差多少？",
    reference_hint: { scope: "baseline", by: "weekday", end_date: CUTOFF } },
  { id: "q-profit", category: "unavailable", title: "利润", question: "算出门店 2023 年上半年的净利润。", reference_hint: null },
  { id: "q-oos", category: "out_of_sample", title: "下半年会不会调价（样本外）", question: "根据历史调价规律，判断截断日之后美式会不会调价，依据是什么？",
    reference_hint: { scope: "baseline", by: "month", product: "美式" } },
] satisfies Omit<EvalQuestion, "post_cutoff_reference">[]).map(q => ({ ...q, post_cutoff_reference: null }));
