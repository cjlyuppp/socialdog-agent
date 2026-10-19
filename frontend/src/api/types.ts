// API contract between the React app and the FastAPI backend.
// The backend's OpenAPI schema must match these shapes; docs/接口约定.md lists the endpoints.
// Errors are always `{ "error": string }` with a non-2xx status.

export type Role = "admin" | "researcher";

export interface Quota {
  daily_limit: number;
  used_today: number;
  remaining_today: number;
  /** Questions left today for the whole site; the third cost guard. */
  site_remaining_today: number;
}

export interface Me {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  /** Sent back as X-CSRF-Token on every mutating request. */
  csrf_token: string;
  quota: Quota;
  /** Each researcher may run at most one job at a time. */
  running_job_id: string | null;
}

export interface InviteInfo {
  username: string;
  display_name: string;
  expires_at: string;
}

/** What the agent can see: only data created on or before cutoff_date. */
export interface DataStatus {
  dataset_version: string;
  cutoff_date: string;
  agent_observation_start: string;
  agent_observation_end: string;
  min_orders_per_group: number;
  model: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Evidence {
  tool: string;
  call_id: string;
  /** JSON string exactly as the model sent it. */
  arguments: string;
  result: Record<string, unknown>;
}

export interface Grounding {
  ungrounded_numbers: string[];
  repair_attempted: boolean;
}

export interface AgentResult {
  answer: string;
  evidence: Evidence[];
  snapshot_versions: string[];
  grounding: Grounding;
  model: string;
  request_id: string;
  usage: { input_tokens: number | null; output_tokens: number | null };
}

export type TurnStatus = "running" | "ok" | "error";

export interface Turn {
  id: string;
  question: string;
  status: TurnStatus;
  created_at: string;
  job_id: string | null;
  result: AgentResult | null;
  error: string | null;
}

export interface SessionDetail extends Session {
  turns: Turn[];
}

export interface ProgressEvent {
  phase: "queued" | "source" | "model" | "tool" | "tool_complete" | "verify" | "complete";
  text: string;
  tool?: string | null;
  at: string;
}

export type JobStatus = "queued" | "running" | "ok" | "error";

export interface Job {
  id: string;
  status: JobStatus;
  session_id: string;
  turn_id: string;
  /** Position in the global queue (max 3 concurrent jobs); null once running. */
  queue_position: number | null;
  events: ProgressEvent[];
  error: string | null;
}

export interface SubmitResponse {
  turn: Turn;
  job: Job;
}

// ---- Reference (数据核对): full data incl. after the cutoff; never sent to the model.

export type ReferenceScope = "baseline" | "all";
export type ReferenceGroup =
  | "all" | "day" | "week" | "month" | "hour" | "weekday" | "category" | "product"
  | "delivery" | "payment" | "status" | "type";

export interface ReferenceMeta {
  dataset_version: string;
  cutoff_date: string;
  observation_start: string;
  observation_end: string;
  groups: { value: ReferenceGroup; label: string }[];
  products: string[];
  can_view_after_cutoff: boolean;
}

export interface ReferenceQuery {
  scope: ReferenceScope;
  by: ReferenceGroup;
  start_date: string | null;
  end_date: string | null;
  product: string | null;
}

export interface ReferenceRow {
  group: string;
  order_count: number;
  line_count: number;
  recorded_quantity: number;
  recorded_receipt_yuan: number;
  recorded_deal_yuan: number;
  wallet_yuan: number;
  coupon_yuan: number;
  /** True when every record in the group was created after the cutoff (the agent cannot see it). */
  after_cutoff: boolean;
}

export interface ReferenceResult {
  query: ReferenceQuery;
  rows: ReferenceRow[];
  group_order_counts_additive: boolean;
  notes: string[];
}

// ---- Evaluation (测评)

export type EvalCategory = "time_series" | "product" | "price_event" | "quality" | "unavailable" | "out_of_sample";

export interface EvalQuestion {
  id: string;
  category: EvalCategory;
  title: string;
  question: string;
  /** Suggested reference query for writing one's own answer first. */
  reference_hint: Partial<ReferenceQuery> | null;
}

export type NumberVerdict = "correct" | "partial" | "wrong" | "not_applicable";

export interface RatingScores {
  numbers: NumberVerdict;
  reasoning: number;
  definitions: number;
  limitations: number;
}

export interface EvalAttempt {
  id: string;
  question_id: string;
  own_answer: string;
  job_id: string;
  turn: Turn;
  rated: boolean;
}

export interface Rating {
  id: string;
  attempt_id: string;
  question_id: string;
  question_title: string;
  question: string;
  own_answer: string;
  agent_answer: string;
  scores: RatingScores;
  note: string;
  created_at: string;
  username: string;
}

export interface RatingInput {
  attempt_id: string;
  scores: RatingScores;
  note: string;
}

// ---- Admin

export interface AdminUser {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  daily_limit: number;
  disabled: boolean;
  used_today: number;
  last_active_at: string | null;
  invite_pending: boolean;
}

export interface NewUser {
  username: string;
  display_name: string;
  role: Role;
  daily_limit: number;
}

export interface UserPatch {
  role?: Role;
  daily_limit?: number;
  disabled?: boolean;
}

export interface InviteLink {
  invite_url: string;
  expires_at: string;
}

export interface UsageLine {
  questions: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_yuan: number | null;
}

export interface UsageReport {
  month: string;
  total: UsageLine;
  by_user: (UsageLine & { user_id: string; username: string; display_name: string })[];
  by_day: (UsageLine & { date: string })[];
}

export interface SiteSettings {
  site_daily_limit: number;
  min_orders_per_group: number;
  cutoff_date: string;
  input_price_per_million_yuan: number | null;
  output_price_per_million_yuan: number | null;
  max_concurrent_jobs: number;
  per_user_concurrent_jobs: number;
  allow_post_cutoff_reference_after_submit: false;
}
