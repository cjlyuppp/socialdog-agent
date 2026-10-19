import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import App from "../App";
import { api, setCsrfToken } from "../api/client";

async function openGuide(path = "/guide") {
  const me = await api.login("lin", "demo");
  setCsrfToken(me.csrf_token);
  await api.acceptNotice();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
  return me;
}

describe("research guide", () => {
  it("opens the example from chat and fills an editable question without creating a task or spending quota", async () => {
    const me = await openGuide("/chat");
    fireEvent.click(await screen.findByRole("link", { name: "跟着范例，完成第一次研究" }));
    await screen.findByRole("heading", { name: "1. 确认数据范围" });
    fireEvent.click(await screen.findByRole("link", { name: "填入研究问题" }));
    const input = await screen.findByRole("textbox", { name: "输入研究问题" });
    expect((input as HTMLTextAreaElement).value).toContain("基准样本定义");
    fireEvent.change(input, { target: { value: "先只告诉我可用数据的时间范围。" } });
    expect((input as HTMLTextAreaElement).value).toBe("先只告诉我可用数据的时间范围。");
    expect(await api.sessions()).toHaveLength(0);
    const current = await api.me();
    expect(current?.quota.remaining_today).toBe(me.quota.remaining_today);
    expect(current?.running_job_id).toBeNull();
  });

  it("continues the selected conversation and keeps that conversation when returning to the example", async () => {
    await api.login("lin", "demo").then(me => setCsrfToken(me.csrf_token));
    const session = await api.createSession();
    await openGuide(`/guide?session=${session.id}&step=3`);
    await screen.findByRole("heading", { name: "3. 比较前后表现" });
    fireEvent.click(await screen.findByRole("link", { name: "填入研究问题" }));
    const input = await screen.findByRole("textbox", { name: "输入研究问题" });
    expect((input as HTMLTextAreaElement).value).toContain("沿用刚才选定的商品");
    fireEvent.change(input, { target: { value: "核对选定商品调价前后的记录件数。" } });
    const send = await screen.findByRole("button", { name: "发送问题" });
    await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(send);
    await screen.findByText("核对选定商品调价前后的记录件数。");
    expect(await api.sessions()).toHaveLength(1);
    expect((await api.session(session.id)).turns[0].question).toBe("核对选定商品调价前后的记录件数。");
    fireEvent.click(screen.getByRole("link", { name: "研究范例" }));
    await screen.findByRole("heading", { name: "研究入门" });
    expect((await screen.findByRole("combobox", { name: "练习使用的对话" })).textContent).toContain("核对选定商品调价前后的记录件数");
  });

  it("recovers from an unavailable conversation before prefilling a question", async () => {
    await openGuide("/guide?session=missing-session");
    expect(await screen.findByText("原对话暂不可用，请选择其他对话或新建研究对话。")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "填入研究问题" })).toBeNull();
    fireEvent.click(screen.getByRole("combobox", { name: "练习使用的对话" }));
    fireEvent.click(screen.getByRole("option", { name: "新建研究对话" }));
    fireEvent.click(await screen.findByRole("link", { name: "填入研究问题" }));
    expect((await screen.findByRole("textbox", { name: "输入研究问题" }) as HTMLTextAreaElement).value).toContain("我想研究商品调价前后的表现");
  });

  it("explains the no-event alternative and connects the final step to submitting a research idea", async () => {
    await openGuide();
    fireEvent.click(await screen.findByRole("button", { name: /找到一次调价/ }));
    fireEvent.click(screen.getByText("没有可识别的调价事件怎么办？"));
    expect(screen.getByText(/按月汇总基准样本的订单数、记录件数/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /整理研究想法/ }));
    expect(screen.getByRole("heading", { name: "5. 整理研究想法" })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "去提交研究想法" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交研究想法" }));
    expect(await screen.findByRole("textbox", { name: "研究问题" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "初步发现" })).toBeTruthy();
  });
});
