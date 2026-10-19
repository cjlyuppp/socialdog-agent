import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import App from "../App";
import { api, setCsrfToken } from "../api/client";
import { db } from "../mocks/db";

function open(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}
async function login(name = "carrey") {
  const me = await api.login(name, "demo");
  setCsrfToken(me.csrf_token);
  await api.acceptNotice();
}

describe("administrator configuration", () => {
  it("keeps edits private until a saved draft is published", async () => {
    await login();
    open("/admin?tab=bank");
    fireEvent.change(await screen.findByLabelText("题目标题"), { target: { value: "正式研究题" } });
    expect((screen.getByRole("button", { name: "发布此版本" }) as HTMLButtonElement).disabled).toBe(true);
    expect((await api.evalQuestions())[0].title).toBe("月度订单走势");
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByText("草稿已保存，研究员仍看到已发布版本。");
    fireEvent.click(screen.getByRole("button", { name: "发布此版本" }));
    await screen.findByText("已发布，新的题目和数据范围已生效。");
    expect((await api.evalQuestions())[0].title).toBe("正式研究题");
    expect(db.bank!.versions.length).toBe(2);
  });

  it("saves rules and uses configured fields in the researcher submission form", async () => {
    await login();
    const view = open("/admin?tab=policy");
    fireEvent.change(await screen.findByLabelText("字段 2 名称"), { target: { value: "研究样本" } });
    fireEvent.change(screen.getByLabelText("审核标准（选填）"), { target: { value: "先明确问题，再检查证据" } });
    fireEvent.click(screen.getAllByRole("checkbox", { name: "必填" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "保存研究规则" }));
    await screen.findByText("已保存");
    view.unmount();
    await login("lin");
    open("/mine");
    fireEvent.click(await screen.findByRole("button", { name: "提交研究想法" }));
    const optional = await screen.findByLabelText("研究样本（选填）");
    expect((optional as HTMLTextAreaElement).required).toBe(false);
    fireEvent.change(screen.getByLabelText("研究问题"), { target: { value: "价格与销量" } });
    fireEvent.change(screen.getByLabelText("初步发现"), { target: { value: "需要控制时间趋势" } });
    fireEvent.change(screen.getByLabelText("下一步想怎么做"), { target: { value: "比较多个窗口" } });
    fireEvent.click(screen.getAllByRole("button", { name: "提交研究想法" }).at(-1)!);
    await screen.findByRole("heading", { name: "价格与销量" });
    expect(db.ideas[0].criteria_snapshot).toBe("先明确问题，再检查证据");
    expect(db.ideas[0].form_snapshot[1].label).toBe("研究样本");
  });

  it("gives a reviewer only the modules they may use", async () => {
    db.users.find(u => u.username === "lin")!.permissions = ["review_feedback"];
    await login("lin");
    open("/admin?tab=settings");
    expect(await screen.findByRole("heading", { name: "审核工作台" })).toBeTruthy();
    await screen.findByText("没有符合条件的问题反馈。");
    expect(screen.getByRole("button", { name: "问题复核" })).toBeTruthy();
    for (const name of ["题库管理", "研究规则", "成员与权限", "额度与运行", "研究想法"]) expect(screen.queryByRole("button", { name })).toBeNull();
    expect(screen.queryByRole("link", { name: "管理后台" })).toBeNull();
  });

  it("collects both anonymous answer scores and reveals the model evidence after submission", async () => {
    db.settings.anonymous_ab_evaluation = true;
    await login("lin");
    const attempt = await api.runEval("q-month", "我的独立答案");
    db.jobs.find(j => j.id === attempt.job_id)!.started_ms -= 60_000;
    await api.job(attempt.job_id);
    open(`/evaluate/${attempt.id}`);
    await screen.findAllByRole("heading", { name: "答案 A" });
    expect(screen.queryByText("研究助理的回答")).toBeNull();
    expect(screen.queryByText(/查看依据/)).toBeNull();
    for (const label of ["答案 A", "答案 B"]) {
      fireEvent.click(screen.getByRole("radio", { name: `${label} 全部正确` }));
      for (const field of ["推理过程", "口径说明", "局限性说明"]) fireEvent.click(screen.getByRole("radio", { name: `${label} ${field} 4 分` }));
    }
    fireEvent.click(screen.getByRole("button", { name: "提交打分" }));
    await screen.findByText("研究助理的回答");
    await waitFor(() => expect(db.ratings[0].anonymous_scores?.A.reasoning).toBe(4));
    expect(await screen.findByRole("button", { name: "这条回答有问题" })).toBeTruthy();
  });
});
