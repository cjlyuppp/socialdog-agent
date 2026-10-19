import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import App from "../App";
import { api } from "../api/client";
import { db } from "../mocks/db";

function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  ) };
}

describe("app", () => {
  it("redirects to login, signs in and lands on the chat workspace", async () => {
    renderApp("/reference");
    fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "carrey" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "demo" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("heading", { name: "数据核对" })).toBeTruthy();
    expect(await screen.findByRole("link", { name: "管理后台" })).toBeTruthy();
  });

  it("shows an invalid invite clearly", async () => {
    renderApp("/invite/nope");
    expect(await screen.findByText(/邀请链接无效或已过期/)).toBeTruthy();
  });

  it("returns an expired session to login, clears private data and restores the requested page", async () => {
    await api.login("lin", "demo");
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
    await api.login("carrey", "demo");
    const { client } = renderApp("/invite/demo-invite");
    await screen.findByRole("heading", { name: "欢迎，周研究员" });
    client.setQueryData(["private-previous-account"], { answer: "previous account data" });
    fireEvent.change(screen.getByLabelText(/新密码/), { target: { value: "only-for-integration" } });
    fireEvent.change(screen.getByLabelText("再次输入"), { target: { value: "only-for-integration" } });
    fireEvent.click(screen.getByRole("button", { name: "设置密码并进入" }));
    await screen.findByRole("heading", { name: "今天想研究什么？" });
    expect(client.getQueryData(["private-previous-account"])).toBeUndefined();
    expect(screen.queryByRole("link", { name: "管理后台" })).toBeNull();
  });
});
