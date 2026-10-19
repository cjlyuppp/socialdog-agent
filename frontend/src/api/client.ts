import type {
  AdminUser, DataStatus, EvalAttempt, EvalQuestion, InviteInfo, InviteLink, Job, Me, NewUser, Rating,
  RatingInput, ReferenceMeta, ReferenceQuery, ReferenceResult, Session, SessionDetail, SiteSettings,
  SubmitResponse, UsageReport, UserPatch,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

let csrfToken = "";
const sessionExpiredListeners = new Set<() => void>();

export function onSessionExpired(listener: () => void) {
  sessionExpiredListeners.add(listener);
  return () => { sessionExpiredListeners.delete(listener); };
}

/** Set from /api/me or a login response; required on every mutating request. */
export function setCsrfToken(token: string) {
  csrfToken = token;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const requestToken = csrfToken;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(path, {
      method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("无法连接服务器，请检查网络后重试。", 0);
  }
  // A delayed response from the previous account must not expire a new login.
  if (response.status === 401 && path !== "/api/auth/login" && requestToken === csrfToken) {
    csrfToken = "";
    sessionExpiredListeners.forEach((listener) => listener());
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(data?.error ?? `请求失败（HTTP ${response.status}）`, response.status);
  return data as T;
}

function query(params: Record<string, string | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? "?" + text : "";
}

export const api = {
  me: () => request<Me>("GET", "/api/me"),
  login: (username: string, password: string) => request<Me>("POST", "/api/auth/login", { username, password }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  invite: (token: string) => request<InviteInfo>("GET", `/api/auth/invite/${encodeURIComponent(token)}`),
  acceptInvite: (token: string, password: string) =>
    request<Me>("POST", "/api/auth/invite/accept", { token, password }),

  status: () => request<DataStatus>("GET", "/api/status"),
  sessions: () => request<Session[]>("GET", "/api/sessions"),
  createSession: () => request<Session>("POST", "/api/sessions"),
  session: (id: string) => request<SessionDetail>("GET", `/api/sessions/${id}`),
  ask: (sessionId: string, question: string) =>
    request<SubmitResponse>("POST", `/api/sessions/${sessionId}/messages`, { question }),
  job: (id: string) => request<Job>("GET", `/api/jobs/${id}`),

  referenceMeta: () => request<ReferenceMeta>("GET", "/api/reference/meta"),
  reference: (q: ReferenceQuery) => request<ReferenceResult>("GET", "/api/reference/query" + query({
    scope: q.scope, by: q.by, start_date: q.start_date, end_date: q.end_date, product: q.product,
  })),

  evalQuestions: () => request<EvalQuestion[]>("GET", "/api/eval/questions"),
  runEval: (questionId: string, ownAnswer: string) =>
    request<EvalAttempt>("POST", `/api/eval/questions/${questionId}/run`, { own_answer: ownAnswer }),
  attempt: (id: string) => request<EvalAttempt>("GET", `/api/eval/attempts/${id}`),
  rate: (input: RatingInput) => request<Rating>("POST", "/api/ratings", input),
  myRatings: () => request<Rating[]>("GET", "/api/ratings/mine"),

  users: () => request<AdminUser[]>("GET", "/api/admin/users"),
  createUser: (user: NewUser) => request<{ user: AdminUser } & InviteLink>("POST", "/api/admin/users", user),
  updateUser: (id: string, patch: UserPatch) => request<AdminUser>("PATCH", `/api/admin/users/${id}`, patch),
  reinvite: (id: string) => request<InviteLink>("POST", `/api/admin/users/${id}/invite`),
  usage: (month: string) => request<UsageReport>("GET", "/api/admin/usage" + query({ month })),
  allRatings: () => request<Rating[]>("GET", "/api/admin/ratings"),
  settings: () => request<SiteSettings>("GET", "/api/admin/settings"),
  saveSettings: (settings: SiteSettings) => request<SiteSettings>("PUT", "/api/admin/settings", settings),
};

export const RATINGS_EXPORT_URL = "/api/admin/ratings/export.csv";
