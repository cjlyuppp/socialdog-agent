// MSW handlers implementing docs/接口约定.md against the in-browser mock state.
import { http, HttpResponse, type DefaultBodyType, type StrictRequest } from "msw";
import type {
  AdminUser, EvalAttempt, Me, NewUser, Rating, RatingInput, ReferenceGroup, ReferenceQuery, ReferenceScope,
  SiteSettings, UserPatch,
} from "../api/types";
import { AGENT_LINES, CUTOFF, DATASET_VERSION, END, GROUP_LABELS, LINES, START, aggregate } from "./data";
import {
  QUESTIONS, advance, db, id, now, runningJob, save, settle, startJob, today, usedToday, type UserRecord,
} from "./db";

const fail = (status: number, error: string) => HttpResponse.json({ error }, { status });

function me(user: UserRecord): Me {
  const used = usedToday(user.id);
  const siteUsed = db.usage.filter((u) => u.date === today()).length;
  return {
    id: user.id, username: user.username, display_name: user.display_name, role: user.role, csrf_token: db.csrf,
    quota: { daily_limit: user.daily_limit, used_today: used, remaining_today: Math.max(0, user.daily_limit - used),
             site_remaining_today: Math.max(0, db.settings.site_daily_limit - siteUsed) },
    running_job_id: runningJob(user.id)?.id ?? null,
  };
}

function adminView(user: UserRecord): AdminUser {
  const { password: _p, invite_token: _t, invite_expires_at: _e, ...rest } = user;
  return { ...rest, used_today: usedToday(user.id) };
}

/** Simulates the session cookie and CSRF check; returns the user or an error response. */
function auth(request: StrictRequest<DefaultBodyType>, role?: "admin") {
  const user = db.users.find((u) => u.id === db.current_user_id && !u.disabled);
  if (!user) return fail(401, "请先登录。");
  if (request.method !== "GET" && request.headers.get("X-CSRF-Token") !== db.csrf) return fail(403, "请求校验失败，请刷新页面。");
  if (role === "admin" && user.role !== "admin") return fail(403, "需要管理员权限。");
  user.last_active_at = now();
  return user;
}

function inviteLink(user: UserRecord) {
  user.invite_token = id();
  user.invite_expires_at = new Date(Date.now() + 3 * 86400_000).toISOString();
  user.invite_pending = true;
  return { invite_url: `${location.origin}/invite/${user.invite_token}`, expires_at: user.invite_expires_at };
}

function checkQuota(user: UserRecord) {
  if (runningJob(user.id)) return fail(409, "你已有一个分析在进行中，请等它完成后再提问。");
  if (usedToday(user.id) >= user.daily_limit) return fail(429, "今天的提问次数已用完，明天再来或联系管理员。");
  if (db.usage.filter((u) => u.date === today()).length >= db.settings.site_daily_limit) {
    return fail(429, "全站今日额度已用完，请明天再试。");
  }
  return null;
}

function attemptView(attempt: (typeof db.attempts)[number]): EvalAttempt {
  const job = db.jobs.find((j) => j.id === attempt.job_id);
  if (job) advance(job);
  const { user_id: _u, turn_id, ...rest } = attempt;
  const { session_id: _s, ...turn } = db.turns.find((t) => t.id === turn_id)!;
  return { ...rest, turn, rated: db.ratings.some((r) => r.attempt_id === attempt.id) };
}

const cost = (input: number, output: number) => {
  const { input_price_per_million_yuan: incoming, output_price_per_million_yuan: outgoing } = db.settings;
  return incoming === null || outgoing === null ? null : Math.round(((input * incoming + output * outgoing) / 1e6) * 100) / 100;
};

function usageLine(records: typeof db.usage) {
  const input = records.reduce((s, r) => s + r.input_tokens, 0);
  const output = records.reduce((s, r) => s + r.output_tokens, 0);
  return { questions: records.length, input_tokens: input, output_tokens: output, cost_yuan: cost(input, output) };
}

export const handlers = [
  http.get("/api/me", () => {
    const user = db.users.find((u) => u.id === db.current_user_id && !u.disabled);
    return user ? HttpResponse.json(me(user)) : fail(401, "请先登录。");
  }),
  http.post("/api/auth/login", async ({ request }) => {
    const { username, password } = (await request.json()) as { username: string; password: string };
    const user = db.users.find((u) => u.username === username);
    if (!user || !user.password || user.password !== password) return fail(401, "用户名或密码不正确。");
    if (user.disabled) return fail(403, "账号已停用，请联系管理员。");
    db.current_user_id = user.id;
    save();
    return HttpResponse.json(me(user));
  }),
  http.post("/api/auth/logout", () => {
    db.current_user_id = null;
    save();
    return new HttpResponse(null, { status: 204 });
  }),
  http.get("/api/auth/invite/:token", ({ params }) => {
    const user = db.users.find((u) => u.invite_token === params.token);
    if (!user || !user.invite_expires_at || user.invite_expires_at < now()) return fail(404, "邀请链接无效或已过期，请联系管理员重新生成。");
    return HttpResponse.json({ username: user.username, display_name: user.display_name, expires_at: user.invite_expires_at });
  }),
  http.post("/api/auth/invite/accept", async ({ request }) => {
    const { token, password } = (await request.json()) as { token: string; password: string };
    const user = db.users.find((u) => u.invite_token === token);
    if (!user || !user.invite_expires_at || user.invite_expires_at < now()) return fail(404, "邀请链接无效或已过期。");
    if (password.length < 10) return fail(400, "密码至少 10 位。");
    Object.assign(user, { password, invite_token: null, invite_expires_at: null, invite_pending: false });
    db.current_user_id = user.id;
    save();
    return HttpResponse.json(me(user));
  }),

  http.get("/api/status", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    return HttpResponse.json({
      dataset_version: DATASET_VERSION, cutoff_date: db.settings.cutoff_date,
      agent_observation_start: AGENT_LINES[0].order_date, agent_observation_end: CUTOFF,
      min_orders_per_group: db.settings.min_orders_per_group, model: "deepseek-flash",
    });
  }),
  http.get("/api/sessions", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    return HttpResponse.json(db.sessions.filter((s) => s.user_id === user.id)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map(({ user_id: _u, ...s }) => s));
  }),
  http.post("/api/sessions", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const session = { id: id(), title: "新的研究", created_at: now(), updated_at: now(), user_id: user.id };
    db.sessions.push(session);
    save();
    const { user_id: _u, ...result } = session;
    return HttpResponse.json(result, { status: 201 });
  }),
  http.get("/api/sessions/:id", ({ request, params }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const session = db.sessions.find((s) => s.id === params.id && s.user_id === user.id);
    if (!session) return fail(404, "未找到该会话。");
    settle();
    const { user_id: _u, ...rest } = session;
    return HttpResponse.json({ ...rest, turns: db.turns.filter((t) => t.session_id === session.id).map(({ session_id: _s, ...t }) => t) });
  }),
  http.post("/api/sessions/:id/messages", async ({ request, params }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const session = db.sessions.find((s) => s.id === params.id && s.user_id === user.id);
    if (!session) return fail(404, "未找到该会话。");
    const { question } = (await request.json()) as { question: string };
    if (!question?.trim() || question.length > 4000) return fail(400, "问题不能为空，且最多 4000 字。");
    const blocked = checkQuota(user);
    if (blocked) return blocked;
    if (session.title === "新的研究") session.title = question.trim().slice(0, 28);
    session.updated_at = now();
    const { turn, job } = startJob(user.id, session.id, question.trim());
    const { session_id: _s, ...publicTurn } = turn as typeof turn & { session_id: string };
    return HttpResponse.json({ turn: publicTurn, job }, { status: 202 });
  }),
  http.get("/api/jobs/:id", ({ request, params }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const job = db.jobs.find((j) => j.id === params.id && j.user_id === user.id);
    return job ? HttpResponse.json(advance(job)) : fail(404, "未找到该任务。");
  }),

  http.get("/api/reference/meta", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const allowed = user.role === "admin" ? LINES : LINES.filter((l) => l.order_date <= db.settings.cutoff_date);
    return HttpResponse.json({
      dataset_version: DATASET_VERSION.replace(/_asof.*/, ""), cutoff_date: db.settings.cutoff_date,
      observation_start: START, observation_end: user.role === "admin" ? END : db.settings.cutoff_date, groups: GROUP_LABELS,
      products: [...new Set(allowed.map((l) => l.product))], can_view_after_cutoff: user.role === "admin",
    });
  }),
  http.get("/api/reference/query", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const params = new URL(request.url).searchParams;
    const by = (params.get("by") ?? "all") as ReferenceGroup;
    const scope = (params.get("scope") ?? "baseline") as ReferenceScope;
    if (!GROUP_LABELS.some((g) => g.value === by) || !["baseline", "all"].includes(scope)) return fail(400, "分组或样本参数不合法。");
    const query: ReferenceQuery = { scope, by, start_date: params.get("start_date"), end_date: params.get("end_date"),
                                    product: params.get("product") };
    if (query.start_date && query.end_date && query.start_date > query.end_date) return fail(400, "开始日期不能晚于结束日期。");
    return HttpResponse.json(aggregate(query, user.role === "admin" ? LINES : LINES.filter((l) => l.order_date <= db.settings.cutoff_date)));
  }),

  http.get("/api/eval/questions", ({ request }) => {
    const user = auth(request);
    return user instanceof Response ? user : HttpResponse.json(QUESTIONS);
  }),
  http.post("/api/eval/questions/:id/run", async ({ request, params }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const question = QUESTIONS.find((q) => q.id === params.id);
    if (!question) return fail(404, "未找到该题目。");
    const { own_answer } = (await request.json()) as { own_answer: string };
    if (!own_answer?.trim()) return fail(400, "请先写下你自己的答案，再揭晓 agent 的回答。");
    const blocked = checkQuota(user);
    if (blocked) return blocked;
    const { turn, job } = startJob(user.id, "eval", question.question);
    const attempt = { id: id(), question_id: question.id, own_answer: own_answer.trim(), job_id: job.id,
                      user_id: user.id, turn_id: turn.id };
    db.attempts.push(attempt);
    save();
    return HttpResponse.json(attemptView(attempt), { status: 202 });
  }),
  http.get("/api/eval/attempts/:id", ({ request, params }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const attempt = db.attempts.find((a) => a.id === params.id && a.user_id === user.id);
    return attempt ? HttpResponse.json(attemptView(attempt)) : fail(404, "未找到该测评。");
  }),
  http.post("/api/ratings", async ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const input = (await request.json()) as RatingInput;
    const attempt = db.attempts.find((a) => a.id === input.attempt_id && a.user_id === user.id);
    if (!attempt) return fail(404, "未找到该测评。");
    if (db.ratings.some((r) => r.attempt_id === attempt.id)) return fail(409, "这次测评已经打过分。");
    const view = attemptView(attempt);
    if (view.turn.status === "running") return fail(409, "agent 还没有回答完。");
    const { reasoning, definitions, limitations } = input.scores;
    if (![reasoning, definitions, limitations].every((s) => Number.isInteger(s) && s >= 1 && s <= 5)) return fail(400, "评分须为 1–5。");
    const question = QUESTIONS.find((q) => q.id === attempt.question_id)!;
    const rating: Rating & { user_id: string } = {
      id: id(), attempt_id: attempt.id, question_id: question.id, question_title: question.title, question: question.question,
      own_answer: attempt.own_answer, agent_answer: view.turn.result?.answer ?? view.turn.error ?? "", scores: input.scores,
      note: input.note ?? "", created_at: now(), username: user.username, user_id: user.id,
    };
    db.ratings.push(rating);
    save();
    const { user_id: _u, ...result } = rating;
    return HttpResponse.json(result, { status: 201 });
  }),
  http.get("/api/ratings/mine", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    return HttpResponse.json(db.ratings.filter((r) => r.user_id === user.id).reverse().map(({ user_id: _u, ...r }) => r));
  }),

  http.get("/api/admin/users", ({ request }) => {
    const user = auth(request, "admin");
    return user instanceof Response ? user : HttpResponse.json(db.users.map(adminView));
  }),
  http.post("/api/admin/users", async ({ request }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const input = (await request.json()) as NewUser;
    if (!/^[a-z0-9_]{2,32}$/.test(input.username)) return fail(400, "用户名只能用 2–32 位小写字母、数字或下划线。");
    if (db.users.some((u) => u.username === input.username)) return fail(409, "用户名已存在。");
    if (db.users.length >= 10) return fail(409, "已达到 10 个账号上限。");
    const user: UserRecord = { id: id(), ...input, disabled: false, last_active_at: null, invite_pending: true,
                               password: null, invite_token: null, invite_expires_at: null };
    db.users.push(user);
    const link = inviteLink(user);
    save();
    return HttpResponse.json({ user: adminView(user), ...link }, { status: 201 });
  }),
  http.patch("/api/admin/users/:id", async ({ request, params }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const user = db.users.find((u) => u.id === params.id);
    if (!user) return fail(404, "未找到该用户。");
    const patch = (await request.json()) as UserPatch;
    if (user.id === admin.id && (patch.disabled || patch.role === "researcher")) return fail(400, "不能停用自己或取消自己的管理员权限。");
    if (patch.daily_limit !== undefined && !(Number.isInteger(patch.daily_limit) && patch.daily_limit >= 0 && patch.daily_limit <= 500)) {
      return fail(400, "每日配额须为 0–500 的整数。");
    }
    Object.assign(user, patch);
    save();
    return HttpResponse.json(adminView(user));
  }),
  http.post("/api/admin/users/:id/invite", ({ request, params }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const user = db.users.find((u) => u.id === params.id);
    if (!user) return fail(404, "未找到该用户。");
    const link = inviteLink(user);
    user.password = null;
    save();
    return HttpResponse.json(link);
  }),
  http.get("/api/admin/usage", ({ request }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const month = new URL(request.url).searchParams.get("month") ?? today().slice(0, 7);
    const records = db.usage.filter((u) => u.date.startsWith(month));
    const dates = [...new Set(records.map((r) => r.date))].sort();
    return HttpResponse.json({
      month, total: usageLine(records),
      by_user: db.users.map((u) => ({ user_id: u.id, username: u.username, display_name: u.display_name,
                                      ...usageLine(records.filter((r) => r.user_id === u.id)) })),
      by_day: dates.map((date) => ({ date, ...usageLine(records.filter((r) => r.date === date)) })),
    });
  }),
  http.get("/api/admin/ratings", ({ request }) => {
    const admin = auth(request, "admin");
    return admin instanceof Response ? admin : HttpResponse.json([...db.ratings].reverse().map(({ user_id: _u, ...r }) => r));
  }),
  http.get("/api/admin/ratings/export.csv", ({ request }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const cell = (v: unknown) => `"${String(v).replaceAll('"', '""')}"`;
    const header = ["时间", "用户", "题目", "自己的答案", "agent回答", "数字", "推理", "口径", "局限性", "备注"];
    const rows = db.ratings.map((r) => [r.created_at, r.username, r.question_title, r.own_answer, r.agent_answer,
      r.scores.numbers, r.scores.reasoning, r.scores.definitions, r.scores.limitations, r.note]);
    return new HttpResponse("﻿" + [header, ...rows].map((r) => r.map(cell).join(",")).join("\n"), {
      headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="ratings.csv"' },
    });
  }),
  http.get("/api/admin/settings", ({ request }) => {
    const admin = auth(request, "admin");
    return admin instanceof Response ? admin : HttpResponse.json(db.settings);
  }),
  http.put("/api/admin/settings", async ({ request }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const input = (await request.json()) as SiteSettings;
    if (input.allow_post_cutoff_reference_after_submit !== false) return fail(400, "提交后开放截止日后数据尚未启用。");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(input.cutoff_date)) return fail(400, "截断日期格式应为 YYYY-MM-DD。");
    if (input.min_orders_per_group < 1 || input.max_concurrent_jobs < 1 || input.site_daily_limit < 0) {
      return fail(400, "门槛、并发和上限须为正数。");
    }
    db.settings = input;
    save();
    return HttpResponse.json(db.settings);
  }),
];
