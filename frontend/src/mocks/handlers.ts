// MSW handlers implementing docs/接口约定.md against the in-browser mock state.
import { http, HttpResponse, type DefaultBodyType, type StrictRequest } from "msw";
import type {
  AdminUser, EvalAttempt, FeedbackInput, FeedbackIssue, IdeaInput, IdeaStatus, Me, NewUser, Rating, RatingInput, ReferenceGroup, ReferenceQuery, ReferenceScope,
  BankDraft, ModelSelection, Permission, ResearchPolicy, ReviewStatus, SiteSettings, UserPatch,
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
    id: user.id, username: user.username, display_name: user.display_name, role: user.role, csrf_token: db.csrf, notice_version: user.notice_version, permissions: user.permissions,
    quota: { daily_limit: user.daily_limit, used_today: used, remaining_today: Math.max(0, user.daily_limit - used),
             site_remaining_today: Math.max(0, db.settings.site_daily_limit - siteUsed), evaluation_reserve: user.evaluation_reserve,
             chat_remaining_today: Math.max(0, user.daily_limit - user.evaluation_reserve - used) },
    running_job_id: runningJob(user.id)?.id ?? null,
  };
}

function adminView(user: UserRecord): AdminUser {
  const { password: _p, invite_token: _t, invite_expires_at: _e, ...rest } = user;
  return { ...rest, used_today: usedToday(user.id) };
}

/** Simulates the session cookie and CSRF check; returns the user or an error response. */
function auth(request: StrictRequest<DefaultBodyType>, role?: "admin" | Permission) {
  const user = db.users.find((u) => u.id === db.current_user_id && !u.disabled);
  if (!user) return fail(401, "请先登录。");
  if (request.method !== "GET" && request.headers.get("X-CSRF-Token") !== db.csrf) return fail(403, "请求校验失败，请刷新页面。");
  if (role === "admin" && user.role !== "admin") return fail(403, "需要管理员权限。");
  if (role && role !== "admin" && user.role !== "admin" && !user.permissions.includes(role)) return fail(403, "你没有此项审核权限。");
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
  const { user_id: _u, turn_id, answer_order, ...rest } = attempt;
  const { session_id: _s, ...turn } = db.turns.find((t) => t.id === turn_id)!;
  const rated = db.ratings.some(r => r.attempt_id === attempt.id);
  const answers: Record<string, string> = { own: attempt.own_answer, agent: turn.result?.answer ?? "" };
  const panels = attempt.anonymous_ab && turn.result ? (answer_order ?? []).map((kind, i) => ({ label: i ? "B" : "A", answer: answers[kind], ...(rated ? { source: kind } : {}) })) : [];
  return { ...rest, answer_panels: panels, turn: { ...turn, result: attempt.anonymous_ab && !rated ? null : turn.result }, rated };
}

function bankWorkspace() {
  if (!db.bank) {
    const active = { id: "demo-bank", name: "演示题库", cutoff_T: CUTOFF, threshold_N: db.settings.min_orders_per_group, dataset_version: DATASET_VERSION, legacy: false, created_at: now(), question_count: QUESTIONS.length, questions: structuredClone(QUESTIONS), active: true };
    db.bank = { active, draft: { revision: 0, name: active.name, cutoff_T: CUTOFF, threshold_N: active.threshold_N, questions: structuredClone(QUESTIONS) }, versions: [active], can_change_cutoff: false, earliest_date: START, latest_date: END };
  }
  return db.bank;
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

function mockIssue(user: UserRecord, input: FeedbackInput): FeedbackIssue | Response {
  const turn = db.turns.find(t => t.id === input.turn_id);
  const job = db.jobs.find(j => j.turn_id === turn?.id && j.user_id === user.id);
  if (!turn?.result || !job) return fail(404, "未找到该回答。");
  const attempt = db.attempts.find(a => a.turn_id === input.turn_id);
  if (attempt?.anonymous_ab && !db.ratings.some(r => r.attempt_id === attempt.id)) return fail(409, "请先完成 A/B 评分。");
  if (!input.description.trim()) return fail(400, "请描述问题。");
  if (input.evidence_refs.some(ref => !turn.result!.evidence.some(e => e.call_id === ref))) return fail(400, "关联依据不属于该回答。");
  return { ...input, id: id(), user_id: user.id, display_name: user.display_name, question: turn.question, model_answer: turn.result.answer, evidence_snapshot: structuredClone(turn.result.evidence), review_status: "pending", review_note: "", reviewed_by: null, reviewed_at: null, created_at: now() };
}

const assignmentView = (a: typeof db.assignments[number]) => ({ ...a, overdue: !a.completed && !!a.due_at && a.due_at < today() });

function referenceLines(user: UserRecord, request: Request): typeof LINES | Response {
  if (user.role === "admin") return LINES;
  const params = new URL(request.url).searchParams;
  const attemptId = params.get("attempt_id"), assignmentId = params.get("assignment_id"), versionId = params.get("bank_version_id");
  if ([attemptId, assignmentId, versionId].filter(Boolean).length > 1) return fail(400, "请只指定一个核对上下文。");
  const attempt = db.attempts.find(a => a.id === attemptId && a.user_id === user.id);
  if (attemptId && !attempt) return fail(404, "未找到该测评。");
  if (assignmentId && !db.assignments.some(a => a.id === assignmentId && a.user_id === user.id)) return fail(404, "未找到该待办。");
  if (versionId && versionId !== bankWorkspace().active.id) return fail(403, "无法访问此版本。");
  const window = attempt && db.settings.allow_post_cutoff_reference_after_submit ? (attempt.question_snapshot as unknown as typeof QUESTIONS[number])?.post_cutoff_reference : null;
  return LINES.filter(l => l.order_date <= db.settings.cutoff_date || (window && l.order_date >= window.start_date && l.order_date <= window.end_date && (!window.product || l.product === window.product)));
}

export const handlers = [
  http.get("/api/research-policy", ({ request }) => {
    const user = auth(request); return user instanceof Response ? user : HttpResponse.json(db.settings);
  }),
  http.patch("/api/admin/research-policy", async ({ request }) => {
    const user = auth(request, "admin"); if (user instanceof Response) return user;
    Object.assign(db.settings, await request.json() as ResearchPolicy); save(); return HttpResponse.json(db.settings);
  }),
  http.get("/api/review/members", ({ request }) => {
    const user = auth(request); if (user instanceof Response) return user;
    if (user.role !== "admin" && !user.permissions.length) return fail(403, "需要审核权限。");
    return HttpResponse.json(db.users.map(u => ({ id: u.id, display_name: u.display_name })));
  }),
  http.get("/api/admin/question-bank", ({ request }) => {
    const user = auth(request, "admin"); return user instanceof Response ? user : HttpResponse.json(bankWorkspace());
  }),
  http.put("/api/admin/question-bank/draft", async ({ request }) => {
    const user = auth(request, "admin"); if (user instanceof Response) return user;
    const draft = await request.json() as BankDraft; const bank = bankWorkspace();
    if (draft.revision !== bank.draft.revision) return fail(409, "草稿已更新，请重新加载。");
    bank.draft = { ...draft, revision: draft.revision + 1 }; save(); return HttpResponse.json(bank);
  }),
  http.post("/api/admin/question-bank/publish", async ({ request }) => {
    const user = auth(request, "admin"); if (user instanceof Response) return user;
    const { revision } = await request.json() as { revision: number }; const bank = bankWorkspace();
    if (revision !== bank.draft.revision) return fail(409, "草稿已更新，请重新加载。");
    if (!bank.draft.questions.length || bank.draft.cutoff_T !== CUTOFF) return fail(400, "演示模式需要题目，且不能修改数据日期。");
    bank.versions.forEach(v => { v.active = false; });
    bank.active = { ...bank.active, ...structuredClone(bank.draft), id: id(), question_count: bank.draft.questions.length, active: true };
    bank.versions.unshift(bank.active); db.settings.min_orders_per_group = bank.draft.threshold_N;
    save(); return HttpResponse.json(bank);
  }),
  http.get("/api/models", ({ request }) => {
    const user = auth(request); return user instanceof Response ? user : HttpResponse.json({ models: [{ id: "deepseek-flash", label: "DeepSeek Flash", reasoning_efforts: ["none", "low", "high", "max"] }, { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro", reasoning_efforts: ["none", "low", "high", "max"] }], default_model: "deepseek-flash", default_reasoning_effort: "high" });
  }),
  http.post("/api/notice/accept", ({ request }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    user.notice_version = 1; save(); return HttpResponse.json(me(user));
  }),
  http.get("/api/assignments/mine", ({ request }) => {
    const user = auth(request); return user instanceof Response ? user : HttpResponse.json(db.assignments.filter(a => a.user_id === user.id).map(assignmentView));
  }),
  http.get("/api/eval/attempts", ({ request }) => {
    const user = auth(request); return user instanceof Response ? user : HttpResponse.json(db.attempts.filter(a => a.user_id === user.id).map(attemptView));
  }),
  http.post("/api/feedback", async ({ request }) => {
    const user = auth(request); if (user instanceof Response) return user;
    const issue = mockIssue(user, await request.json() as FeedbackInput);
    if (issue instanceof Response) return issue;
    db.feedback.push(issue); save(); return HttpResponse.json(issue, { status: 201 });
  }),
  http.get("/api/feedback/mine", ({ request }) => {
    const user = auth(request); return user instanceof Response ? user : HttpResponse.json(db.feedback.filter(i => i.user_id === user.id));
  }),
  http.get("/api/admin/feedback", ({ request }) => {
    const user = auth(request, "review_feedback"); if (user instanceof Response) return user;
    const filters = new URL(request.url).searchParams;
    return HttpResponse.json(db.feedback.filter(i => ["source", "stage", "user_id", "review_status"].every(key => !filters.get(key) || filters.get(key) === i[key as keyof FeedbackIssue])));
  }),
  http.patch("/api/admin/feedback/:id", async ({ request, params }) => {
    const user = auth(request, "review_feedback"); if (user instanceof Response) return user;
    const issue = db.feedback.find(i => i.id === params.id); if (!issue) return fail(404, "未找到问题反馈。");
    const body = await request.json() as { review_status: ReviewStatus; review_note: string };
    Object.assign(issue, body, { reviewed_by: user.id, reviewed_at: now() }); save(); return HttpResponse.json(issue);
  }),
  http.get("/api/admin/improvements/export.json", ({ request }) => {
    const user = auth(request, "review_feedback"); return user instanceof Response ? user : HttpResponse.json(db.feedback.filter(i => i.review_status === "confirmed"), { headers: { "Content-Disposition": 'attachment; filename="model-improvements.json"' } });
  }),
  http.post("/api/ideas", async ({ request }) => {
    const user = auth(request); if (user instanceof Response) return user;
    const body = await request.json() as IdeaInput;
    if (db.settings.idea_fields.some(f => f.required && !body[f.key]?.trim())) return fail(400, "请填写必填项。");
    if (body.linked_conversations.some(id => !db.sessions.some(s => s.id === id && s.user_id === user.id))) return fail(404, "未找到该对话。");
    const idea = { ...body, id: id(), user_id: user.id, display_name: user.display_name, status: "pending" as const, reviewer_feedback: "", reviewed_by: null, created_at: now(), updated_at: now(), form_snapshot: structuredClone(db.settings.idea_fields), criteria_snapshot: db.settings.invitation_criteria };
    db.ideas.push(idea); save(); return HttpResponse.json(idea, { status: 201 });
  }),
  http.get("/api/ideas/mine", ({ request }) => {
    const user = auth(request); return user instanceof Response ? user : HttpResponse.json(db.ideas.filter(i => i.user_id === user.id));
  }),
  http.get("/api/admin/ideas", ({ request }) => {
    const user = auth(request, "review_ideas"); return user instanceof Response ? user : HttpResponse.json(db.ideas);
  }),
  http.patch("/api/admin/ideas/:id", async ({ request, params }) => {
    const user = auth(request, "review_ideas"); if (user instanceof Response) return user;
    const idea = db.ideas.find(i => i.id === params.id); if (!idea) return fail(404, "未找到研究想法。");
    const body = await request.json() as { status: IdeaStatus; reviewer_feedback: string };
    Object.assign(idea, body, { reviewed_by: user.id, updated_at: now() }); save(); return HttpResponse.json(idea);
  }),
  http.get("/api/admin/assignments", ({ request }) => {
    const user = auth(request, "admin"); return user instanceof Response ? user : HttpResponse.json(db.assignments.map(assignmentView));
  }),
  http.post("/api/admin/assignments", async ({ request }) => {
    const user = auth(request, "admin"); if (user instanceof Response) return user;
    const body = await request.json() as { user_id: string; question_id: string; bank_version_id: string; due_at: string | null };
    const question = bankWorkspace().versions.find(v => v.id === body.bank_version_id)?.questions.find(q => q.id === body.question_id); const owner = db.users.find(u => u.id === body.user_id && u.role === "researcher");
    if (!question || !owner) return fail(404, "请选择研究员和题目。");
    if (db.assignments.some(a => a.user_id === body.user_id && a.question_id === body.question_id)) return fail(409, "已分配该题目。");
    const a = { ...body, id: id(), display_name: owner.display_name, assigned_by: user.id, created_at: now(), attempt_id: null, completed: false, overdue: false, question: { ...question, bank_version_id: body.bank_version_id } };
    db.assignments.push(a); save(); return HttpResponse.json(assignmentView(a), { status: 201 });
  }),
  http.get("/api/admin/progress", ({ request }) => {
    const user = auth(request, "admin"); if (user instanceof Response) return user;
    return HttpResponse.json(db.users.filter(u => u.role === "researcher").map(u => ({ user_id: u.id, display_name: u.display_name, assigned: db.assignments.filter(a => a.user_id === u.id).length, completed: db.assignments.filter(a => a.user_id === u.id && a.completed).length, overdue: db.assignments.filter(a => a.user_id === u.id && assignmentView(a).overdue).length })));
  }),
  http.get("/api/admin/question-bank/versions", ({ request }) => {
    const user = auth(request, "admin"); return user instanceof Response ? user : HttpResponse.json(bankWorkspace().versions.map(({ questions: _q, name: _n, active: _a, ...v }) => v));
  }),
  http.get("/api/admin/researchers/:id", ({ request, params }) => {
    const admin = auth(request, "view_researchers"); if (admin instanceof Response) return admin;
    const user = db.users.find(u => u.id === params.id); if (!user) return fail(404, "未找到成员。");
    const issues = db.feedback.filter(i => i.user_id === user.id); const confirmed = issues.filter(i => i.review_status === "confirmed").length; const reviewed = issues.filter(i => ["confirmed", "rejected"].includes(i.review_status)).length;
    const jobs = db.jobs.filter(j => j.user_id === user.id && j.session_id !== "eval");
    return HttpResponse.json({ user: adminView(user), quota: me(user).quota, question_count: jobs.length, recent_questions: jobs.map(j => ({ id: j.turn_id, question: j.question, session_id: j.session_id, created_at: now() })).reverse().slice(0, 20), ratings: db.ratings.filter(r => r.user_id === user.id), feedback_count: issues.length, confirmed_count: confirmed, reviewed_count: reviewed, confirmed_ratio: reviewed ? confirmed / reviewed : null, ideas: db.ideas.filter(i => i.user_id === user.id), assignments: db.assignments.filter(a => a.user_id === user.id).map(assignmentView) });
  }),
  http.get("/api/admin/researchers/:userId/sessions/:sessionId", ({ request, params }) => {
    const admin = auth(request, "view_researchers"); if (admin instanceof Response) return admin;
    const owner = db.users.find(u => u.id === params.userId); if (!owner?.notice_version) return fail(403, "该成员尚未确认使用须知。");
    const session = db.sessions.find(s => s.id === params.sessionId && s.user_id === params.userId); return session ? HttpResponse.json({ ...session, turns: db.turns.filter(t => t.session_id === session.id) }) : fail(404, "未找到对话。");
  }),
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
    const { question, ...selection } = (await request.json()) as { question: string } & ModelSelection;
    if (!question?.trim() || question.length > 4000) return fail(400, "问题不能为空，且最多 4000 字。");
    const blocked = checkQuota(user);
    if (blocked) return blocked;
    if (session.title === "新的研究") session.title = question.trim().slice(0, 28);
    session.updated_at = now();
    const { turn, job } = startJob(user.id, session.id, question.trim(), selection);
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
    const allowed = referenceLines(user, request);
    if (allowed instanceof Response) return allowed;
    return HttpResponse.json({
      dataset_version: DATASET_VERSION.replace(/_asof.*/, ""), cutoff_date: db.settings.cutoff_date,
      observation_start: allowed[0]?.order_date ?? db.settings.cutoff_date, observation_end: allowed.at(-1)?.order_date ?? db.settings.cutoff_date, groups: GROUP_LABELS,
      products: [...new Set(allowed.map((l) => l.product))], can_view_after_cutoff: user.role === "admin" || allowed.some(l => l.order_date > db.settings.cutoff_date),
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
    const allowed = referenceLines(user, request);
    return allowed instanceof Response ? allowed : HttpResponse.json(aggregate(query, allowed));
  }),

  http.get("/api/eval/questions", ({ request }) => {
    const user = auth(request);
    const bank = bankWorkspace();
    return user instanceof Response ? user : HttpResponse.json(bank.active.questions.map(q => ({ ...q, bank_version_id: bank.active.id })));
  }),
  http.post("/api/eval/questions/:id/run", async ({ request, params }) => {
    const user = auth(request);
    if (user instanceof Response) return user;
    const question = bankWorkspace().active.questions.find((q) => q.id === params.id);
    if (!question) return fail(404, "未找到该题目。");
    const { own_answer, model, reasoning_effort } = (await request.json()) as { own_answer: string } & ModelSelection;
    if (!own_answer?.trim()) return fail(400, "请先写下你自己的答案，再揭晓 agent 的回答。");
    const blocked = checkQuota(user);
    if (blocked) return blocked;
    const { turn, job } = startJob(user.id, "eval", question.question, { model, reasoning_effort });
    const attempt = { id: id(), question_id: question.id, own_answer: own_answer.trim(), job_id: job.id,
                      user_id: user.id, turn_id: turn.id, bank_version_id: bankWorkspace().active.id, question_snapshot: { ...question }, anonymous_ab: db.settings.anonymous_ab_evaluation, answer_panels: [], answer_order: Math.random() < .5 ? ["own", "agent"] : ["agent", "own"] };
    db.attempts.push(attempt);
    for (const a of db.assignments.filter(a => a.user_id === user.id && a.question_id === question.id && !a.completed)) Object.assign(a, { completed: true, attempt_id: attempt.id });
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
    const agentLabel = attempt.answer_order?.[0] === "agent" ? "A" : "B";
    const scores = attempt.anonymous_ab ? input.anonymous_scores?.[agentLabel] : input.scores;
    if (!scores || (attempt.anonymous_ab && (!input.anonymous_scores?.A || !input.anonymous_scores?.B))) return fail(400, "请填写全部评分。");
    const { reasoning, definitions, limitations } = scores;
    if (![reasoning, definitions, limitations].every((s) => Number.isInteger(s) && s >= 1 && s <= 5)) return fail(400, "评分须为 1–5。");
    const question = attempt.question_snapshot as unknown as typeof QUESTIONS[number];
    const rating: Rating & { user_id: string } = {
      id: id(), attempt_id: attempt.id, question_id: question.id, question_title: question.title, question: question.question,
      own_answer: attempt.own_answer, agent_answer: db.turns.find(t => t.id === attempt.turn_id)?.result?.answer ?? "", scores, anonymous_scores: input.anonymous_scores ?? null,
      note: input.note ?? "", created_at: now(), username: user.username, user_id: user.id,
    };
    const issues = (input.issues ?? []).map(draft => mockIssue(user, { ...draft, source: "evaluation", turn_id: view.turn.id, attempt_id: attempt.id }));
    const invalid = issues.find(i => i instanceof Response);
    if (invalid instanceof Response) return invalid;
    db.feedback.push(...issues as FeedbackIssue[]);
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
    if (db.users.length >= db.settings.max_accounts) return fail(409, "已达到账号数量上限。");
    const user: UserRecord = { id: id(), ...input, daily_limit: input.daily_limit ?? db.settings.default_daily_limit, evaluation_reserve: input.evaluation_reserve ?? db.settings.default_evaluation_reserve, permissions: input.permissions ?? [], notice_version: 0, disabled: false, last_active_at: null, invite_pending: true,
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
  http.patch("/api/admin/settings", async ({ request }) => {
    const admin = auth(request, "admin"); if (admin instanceof Response) return admin;
    Object.assign(db.settings, await request.json()); save(); return HttpResponse.json(db.settings);
  }),
  http.put("/api/admin/settings", async ({ request }) => {
    const admin = auth(request, "admin");
    if (admin instanceof Response) return admin;
    const input = (await request.json()) as SiteSettings;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(input.cutoff_date)) return fail(400, "截断日期格式应为 YYYY-MM-DD。");
    if (input.min_orders_per_group < 1 || input.max_concurrent_jobs < 1 || input.site_daily_limit < 0) {
      return fail(400, "门槛、并发和上限须为正数。");
    }
    db.settings = input;
    save();
    return HttpResponse.json(db.settings);
  }),
];
