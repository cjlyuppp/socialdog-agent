import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import App from "../App";
import { api, setCsrfToken } from "../api/client";
import { db } from "../mocks/db";

function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  ) };
}

async function signIn(username: string) {
  const me = await api.login(username, "demo");
  setCsrfToken(me.csrf_token);
  await api.acceptNotice();
  return me;
}

describe("app", () => {
  it("redirects to login, signs in and lands on the chat workspace", async () => {
    renderApp("/reference");
    fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "carrey" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    fireEvent.click(await screen.findByRole("button", { name: "了解并进入工作台" }));
    expect(await screen.findByRole("heading", { name: "数据核对" })).toBeTruthy();
    expect(await screen.findByRole("link", { name: "管理后台" })).toBeTruthy();
  });

  it("shows an invalid invite clearly", async () => {
    renderApp("/invite/nope");
    expect(await screen.findByText(/邀请链接无效或已过期/)).toBeTruthy();
  });

  it("returns an expired session to login, clears private data and restores the requested page", async () => {
    await signIn("lin");
    const { client } = renderApp("/chat");
    await screen.findByRole("heading", { name: "今天想研究什么？" });
    client.setQueryData(["private-previous-account"], { answer: "previous account data" });
    db.current_user_id = null;
    fireEvent.click(screen.getByRole("link", { name: "我的测评" }));
    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeTruthy();
    expect(client.getQueryData(["private-previous-account"])).toBeUndefined();
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "carrey" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    fireEvent.click(await screen.findByRole("button", { name: "了解并进入工作台" }));
    expect(await screen.findByRole("heading", { name: "我的测评" })).toBeTruthy();
    expect(await screen.findByRole("link", { name: "管理后台" })).toBeTruthy();
  });

  it("keeps a bad-password error visible on the login form", async () => {
    renderApp("/login");
    fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "lin" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText(/用户名或密码不正确/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
  });

  it("clears the previous account cache when accepting an invitation while signed in", async () => {
    await signIn("carrey");
    const { client } = renderApp("/invite/demo-invite");
    await screen.findByRole("heading", { name: "欢迎，周研究员" });
    client.setQueryData(["private-previous-account"], { answer: "previous account data" });
    fireEvent.change(screen.getByLabelText(/新密码/), { target: { value: "only-for-integration" } });
    fireEvent.change(screen.getByLabelText("再次输入"), { target: { value: "only-for-integration" } });
    fireEvent.click(screen.getByRole("button", { name: "设置密码并进入" }));
    fireEvent.click(await screen.findByRole("button", { name: "了解并进入工作台" }));
    await screen.findByRole("heading", { name: "今天想研究什么？" });
    expect(client.getQueryData(["private-previous-account"])).toBeUndefined();
    expect(screen.queryByRole("link", { name: "管理后台" })).toBeNull();
  });

  it("opens assigned tasks on the default landing page", async () => {
    await signIn("carrey");
    const reader = (await api.users()).find(u => u.username === "lin")!;
    const question = (await api.evalQuestions())[0];
    await api.assign({ user_id: reader.id, question_id: question.id, bank_version_id: question.bank_version_id, due_at: "2020-01-01" });
    await signIn("lin");
    renderApp("/home");
    expect(await screen.findByRole("heading", { name: "我的待办" })).toBeTruthy();
    expect(await screen.findByText("已逾期")).toBeTruthy();
    expect(screen.getByRole("link", { name: "开始作答" }).getAttribute("href")).toContain("assignment=");
  });

  it("submits a research idea with a linked conversation and displays the saved record", async () => {
    await signIn("lin");
    const conversation = await api.createSession();
    renderApp("/mine");
    fireEvent.click(await screen.findByRole("button", { name: "提交研究想法" }));
    await screen.findByLabelText("研究问题");
    for (const [label, value] of [["研究问题", "商品价格是否影响销量"], ["用到的数据", "历史订单"], ["初步发现", "商品间差异较大"], ["下一步想怎么做", "分商品比较"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(await screen.findByRole("checkbox", { name: "新的研究" }));
    const buttons = screen.getAllByRole("button", { name: "提交研究想法" });
    fireEvent.click(buttons[buttons.length - 1]);
    expect(await screen.findByRole("heading", { name: "商品价格是否影响销量" })).toBeTruthy();
    expect((await api.myIdeas())[0].linked_conversations).toEqual([conversation.id]);
    expect(screen.getByText("待审")).toBeTruthy();
  });

  it("submits a problem from a model answer with an evidence reference", async () => {
    await signIn("lin");
    const session = await api.createSession();
    const submitted = await api.ask(session.id, "按月汇总订单");
    db.jobs.find(j => j.id === submitted.job.id)!.started_ms -= 60_000;
    await api.job(submitted.job.id);
    renderApp(`/chat/${session.id}`);
    fireEvent.click(await screen.findByRole("button", { name: "这条回答有问题" }));
    fireEvent.change(screen.getByLabelText("问题描述"), { target: { value: "没有清楚说明样本范围" } });
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
    expect(await screen.findByText(/反馈已提交/)).toBeTruthy();
    const [issue] = await api.myFeedback();
    expect(issue.description).toBe("没有清楚说明样本范围");
    expect(issue.evidence_refs.length).toBe(1);
  });

  it("remembers model and effort choices for the current account and sends them with the question", async () => {
    const me = await signIn("lin");
    const app = renderApp("/chat");
    fireEvent.click(await screen.findByRole("combobox", { name: /^模型：/ }));
    fireEvent.click(screen.getByRole("option", { name: "DeepSeek V4 Pro" }));
    fireEvent.click(screen.getByRole("combobox", { name: /^思考强度：/ }));
    fireEvent.click(screen.getByRole("option", { name: "最高" }));
    fireEvent.change(screen.getByRole("textbox", { name: "输入研究问题" }), { target: { value: "按月汇总订单" } });
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(db.jobs.length).toBe(1));
    expect(db.jobs[0].selection).toEqual({ model: "deepseek-v4-pro", reasoning_effort: "max" });
    expect(JSON.parse(window.localStorage.getItem(`socialdog-model-${me.id}`)!)).toMatchObject({ model: "deepseek-v4-pro", reasoning_effort: "max" });
    app.unmount();
    renderApp("/evaluate?q=q-month");
    expect(await screen.findByRole("combobox", { name: "模型：DeepSeek V4 Pro" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "思考强度：最高" })).toBeTruthy();
  });

  it("supports keyboard navigation and dismisses the model menu without changing the choice", async () => {
    await signIn("lin");
    renderApp("/chat");
    const trigger = await screen.findByRole("combobox", { name: "模型：DeepSeek Flash" });
    trigger.focus();
    fireEvent.click(trigger);
    const selected = screen.getByRole("option", { name: "DeepSeek Flash" });
    expect(trigger.getAttribute("aria-activedescendant")).toBe(selected.id);
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(trigger.getAttribute("aria-activedescendant")).toBe(screen.getByRole("option", { name: "DeepSeek V4 Pro" }).id);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByRole("heading", { name: "今天想研究什么？" }));
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.getByRole("combobox", { name: "模型：DeepSeek Flash" })).toBeTruthy();
  });
});
