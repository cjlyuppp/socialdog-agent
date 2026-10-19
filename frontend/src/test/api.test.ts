import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { ApiError, api, onSessionExpired, setCsrfToken } from "../api/client";
import { CUTOFF } from "../mocks/data";
import { server } from "./setup";

async function login(username = "lin") {
  const me = await api.login(username, "demo");
  setCsrfToken(me.csrf_token);
  return me;
}

function finishJobs() {
  const real = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(real + 60_000);
}

describe("mock backend contract", () => {
  it("does not expire a new login when a previous session request returns 401 late", async () => {
    let release!: () => void;
    let started!: () => void;
    const entered = new Promise<void>((resolve) => { started = resolve; });
    const gate = new Promise<void>((resolve) => { release = resolve; });
    server.use(http.get("/api/sessions", async () => {
      started();
      await gate;
      return HttpResponse.json({ error: "请先登录。" }, { status: 401 });
    }));
    const expired = vi.fn();
    const unsubscribe = onSessionExpired(expired);
    try {
      setCsrfToken("previous-session");
      const pending = api.sessions().catch((error: unknown) => error);
      await entered;
      setCsrfToken("new-session");
      release();
      expect(await pending).toMatchObject({ status: 401 });
      expect(expired).not.toHaveBeenCalled();
    } finally {
      release();
      unsubscribe();
    }
  });

  it("rejects unauthenticated requests and bad passwords", async () => {
    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    await expect(api.login("lin", "wrong")).rejects.toBeInstanceOf(ApiError);
  });

  it("requires the CSRF token on mutations", async () => {
    await login();
    setCsrfToken("forged");
    await expect(api.createSession()).rejects.toMatchObject({ status: 403 });
  });

  it("runs a question to completion, charges quota once and blocks a second concurrent job", async () => {
    const me = await login();
    const session = await api.createSession();
    const { job } = await api.ask(session.id, "按月汇总订单");
    expect(["queued", "running"]).toContain(job.status);
    await expect(api.ask(session.id, "再问一个")).rejects.toMatchObject({ status: 409 });
    finishJobs();
    const done = await api.job(job.id);
    expect(done.status).toBe("ok");
    const detail = await api.session(session.id);
    const result = detail.turns[0].result!;
    expect(result.evidence.map((e) => e.tool)).toContain("history_aggregate");
    // The agent never sees data after the cutoff.
    expect(JSON.stringify(result.evidence)).not.toContain("2023-07");
    expect((await api.me()).quota.used_today).toBe(me.quota.used_today + 1);
    vi.restoreAllMocks();
  });

  it("reference data covers the full period and marks rows after the cutoff", async () => {
    await login("carrey");
    const result = await api.reference({ scope: "baseline", by: "month", start_date: null, end_date: null, product: null });
    const after = result.rows.filter((r) => r.after_cutoff);
    expect(after.length).toBe(6);
    expect(after.every((r) => r.group > CUTOFF.slice(0, 7))).toBe(true);
  });

  it("researcher reference queries and export input never contain post-cutoff rows", async () => {
    await login();
    const meta = await api.referenceMeta();
    expect(meta.can_view_after_cutoff).toBe(false);
    expect(meta.observation_end <= CUTOFF).toBe(true);
    const query = { scope: "baseline" as const, by: "month" as const, start_date: null, end_date: null, product: null };
    expect((await api.reference(query)).rows.every((row) => !row.after_cutoff)).toBe(true);
    expect((await api.reference({ ...query, start_date: "2023-07-01" })).rows).toEqual([]);
  });

  it("evaluation: own answer first, then rate once", async () => {
    await login();
    const [question] = await api.evalQuestions();
    await expect(api.runEval(question.id, " ")).rejects.toMatchObject({ status: 400 });
    const attempt = await api.runEval(question.id, "三月最高");
    await expect(api.rate({ attempt_id: attempt.id, scores: { numbers: "correct", reasoning: 4, definitions: 4, limitations: 3 }, note: "" }))
      .rejects.toMatchObject({ status: 409 });
    finishJobs();
    expect((await api.attempt(attempt.id)).turn.status).toBe("ok");
    const rating = await api.rate({ attempt_id: attempt.id, scores: { numbers: "partial", reasoning: 4, definitions: 5, limitations: 3 }, note: "漏了口径" });
    expect(rating.own_answer).toBe("三月最高");
    expect((await api.myRatings()).map((r) => r.id)).toEqual([rating.id]);
    await expect(api.rate({ attempt_id: attempt.id, scores: rating.scores, note: "" })).rejects.toMatchObject({ status: 409 });
    vi.restoreAllMocks();
  });

  it("admin endpoints are closed to researchers; admins can invite and the invite works once", async () => {
    await login("lin");
    await expect(api.users()).rejects.toMatchObject({ status: 403 });
    await login("carrey");
    const created = await api.createUser({ username: "wang", display_name: "王研究员", role: "researcher", daily_limit: 10 });
    const token = created.invite_url.split("/invite/")[1];
    expect((await api.invite(token)).username).toBe("wang");
    const me = await api.acceptInvite(token, "a-long-password");
    expect(me.username).toBe("wang");
    await expect(api.invite(token)).rejects.toMatchObject({ status: 404 });
  });

  it("quota exhaustion refuses before running the model", async () => {
    await login("carrey");
    const lin = (await api.users()).find((u) => u.username === "lin")!;
    await api.updateUser(lin.id, { daily_limit: 0 });
    await login("lin");
    const session = await api.createSession();
    await expect(api.ask(session.id, "总计")).rejects.toMatchObject({ status: 429 });
  });
});
