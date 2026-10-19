import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BankDraft, EvalQuestion, ReferenceGroup, ResearchPolicy } from "../api/types";
import { Badge, Button, Empty, Field, Input, Notice, Select, Spinner, Textarea } from "../components/ui";
import { dateTime, errorText } from "../lib/format";
import { CATEGORY_LABELS } from "./EvaluatePage";

export function BankManager() {
  const client = useQueryClient();
  const workspace = useQuery({ queryKey: ["bank-workspace"], queryFn: api.bankWorkspace });
  const [draft, setDraft] = useState<BankDraft | null>(null);
  const [selected, setSelected] = useState(0);
  const [message, setMessage] = useState("");
  useEffect(() => { if (workspace.data) setDraft(workspace.data.draft); }, [workspace.data]);
  const refresh = (data: Awaited<ReturnType<typeof api.bankWorkspace>>) => {
    client.setQueryData(["bank-workspace"], data);
    client.invalidateQueries({ queryKey: ["eval-questions"] });
    client.invalidateQueries({ queryKey: ["bank-versions"] });
    client.invalidateQueries({ queryKey: ["admin-settings"] });
    client.invalidateQueries({ queryKey: ["status"] });
    client.invalidateQueries({ queryKey: ["reference-meta"] });
    client.invalidateQueries({ queryKey: ["reference"] });
  };
  const save = useMutation({ mutationFn: () => api.saveBankDraft(draft!), onSuccess: data => { refresh(data); setMessage("草稿已保存，研究员仍看到已发布版本。"); } });
  const publish = useMutation({ mutationFn: () => api.publishBank(draft!.revision), onSuccess: data => { refresh(data); setMessage("已发布，新的题目和数据范围已生效。"); } });
  if (workspace.isPending) return <Spinner />;
  if (workspace.error) return <Notice>{errorText(workspace.error)}</Notice>;
  if (!draft) return <Spinner />;
  const data = workspace.data;
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.draft);
  const busy = save.isPending || publish.isPending;
  const question = draft.questions[selected];
  const changeQuestion = (patch: Partial<EvalQuestion>) => setDraft({ ...draft, questions: draft.questions.map((q, i) => i === selected ? { ...q, ...patch } : q) });
  const add = () => {
    let index = draft.questions.length + 1;
    while (draft.questions.some(q => q.id === `Q${index}`)) index++;
    setSelected(draft.questions.length);
    setDraft({ ...draft, questions: [...draft.questions, { id: `Q${index}`, title: "", question: "", category: "time_series", reference_hint: null, post_cutoff_reference: null }] });
  };
  return <div className="space-y-7">
    <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-5">
      <div><h2 className="text-xl font-semibold">题库管理</h2><p className="mt-2 text-sm text-muted">当前发布：{data.active.name} · {data.active.question_count} 题 · T {data.active.cutoff_T} · N {data.active.threshold_N}</p></div>
      <div className="flex flex-wrap items-center gap-2">{dirty && <Badge>草稿未保存</Badge>}<Button disabled={busy || !dirty} onClick={() => save.mutate()}>保存草稿</Button><Button variant="primary" disabled={busy || dirty || !draft.questions.length} onClick={() => publish.mutate()}>{publish.isPending ? "正在生成数据并发布…" : "发布此版本"}</Button></div>
    </div>
    {(save.error || publish.error) && <Notice>{errorText(save.error ?? publish.error)} <Button variant="ghost" onClick={() => workspace.refetch()}>重新加载</Button></Notice>}
    {message && !dirty && <Notice tone="ok">{message}</Notice>}
    <div className="grid gap-4 md:grid-cols-3">
      <Field label="题库名称"><Input required maxLength={120} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></Field>
      <Field label="数据截止日 T" hint={data.can_change_cutoff ? `已导入 ${data.earliest_date} 至 ${data.latest_date}` : "需要部署人员安装同源完整数据包后才能修改"}><Input type="date" min={data.earliest_date} max={data.latest_date} disabled={!data.can_change_cutoff} value={draft.cutoff_T} onChange={e => setDraft({ ...draft, cutoff_T: e.target.value })} /></Field>
      <Field label="每组最少订单数 N"><Input type="number" min={1} max={10000} value={draft.threshold_N} onChange={e => setDraft({ ...draft, threshold_N: Number(e.target.value) })} /></Field>
    </div>
    <div className="grid gap-6 border-y border-line py-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <div><div className="mb-3 flex items-center justify-between"><h3 className="font-medium">题目 · {draft.questions.length}</h3><Button disabled={busy || draft.questions.length >= 100} onClick={add}>添加题目</Button></div><div className="space-y-1">{draft.questions.map((q, i) => <button key={i} className={`w-full rounded-lg px-3 py-3 text-left text-sm ${selected === i ? "bg-accent-soft text-accent" : "hover:bg-sidebar"}`} onClick={() => setSelected(i)}><span className="mr-2 text-muted">{q.id}</span>{q.title || "未命名题目"}</button>)}</div></div>
      {question ? <div className="min-w-0 space-y-5" key={selected}>
        <div className="grid gap-4 sm:grid-cols-2"><Field label="题目编号"><Input value={question.id} maxLength={80} onChange={e => changeQuestion({ id: e.target.value })} /></Field><Field label="题目分类"><Select value={question.category} onValueChange={e => changeQuestion({ category: e as EvalQuestion["category"] })}>{Object.entries(CATEGORY_LABELS).map(([k, v]) => <option value={k} key={k}>{v}</option>)}</Select></Field></div>
        <Field label="题目标题"><Input maxLength={120} value={question.title} onChange={e => changeQuestion({ title: e.target.value })} /></Field>
        <Field label="研究问题"><Textarea rows={5} maxLength={4000} value={question.question} onChange={e => changeQuestion({ question: e.target.value })} /></Field>
        <details className="rounded-xl bg-sidebar p-4"><summary className="cursor-pointer font-medium">参考数据与提交后核对范围</summary><div className="mt-5 space-y-5">
          <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={!!question.reference_hint} onChange={e => changeQuestion({ reference_hint: e.target.checked ? { scope: "baseline", by: "day" } : null })} />作答时显示参考数据</label>
          {question.reference_hint && <div className="grid gap-3 sm:grid-cols-2"><Field label="参考数据分组"><Select value={question.reference_hint.by ?? "day"} onValueChange={e => changeQuestion({ reference_hint: { ...question.reference_hint, by: e as ReferenceGroup } })}>{[["all", "总计"], ["day", "按日"], ["week", "按周"], ["month", "按月"], ["hour", "按小时"], ["weekday", "按星期"], ["category", "按分类"], ["product", "按商品"], ["delivery", "按配送方式"], ["payment", "按收款方式"], ["status", "按订单状态"], ["type", "按交易类型"]].map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field><Field label="参考样本"><Select value={question.reference_hint.scope ?? "baseline"} onValueChange={e => changeQuestion({ reference_hint: { ...question.reference_hint, scope: e as "baseline" | "all" } })}><option value="baseline">基准样本</option><option value="all">全部记录</option></Select></Field><Field label="参考开始日（选填）"><Input type="date" value={question.reference_hint.start_date ?? ""} onChange={e => changeQuestion({ reference_hint: { ...question.reference_hint, start_date: e.target.value || null } })} /></Field><Field label="参考结束日（选填）"><Input type="date" value={question.reference_hint.end_date ?? ""} onChange={e => changeQuestion({ reference_hint: { ...question.reference_hint, end_date: e.target.value || null } })} /></Field><Field label="参考商品（选填）"><Input value={question.reference_hint.product ?? ""} onChange={e => changeQuestion({ reference_hint: { ...question.reference_hint, product: e.target.value || null } })} /></Field></div>}
          <label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={!!question.post_cutoff_reference} onChange={e => changeQuestion({ post_cutoff_reference: e.target.checked ? { start_date: "", end_date: "", product: null } : null })} />为本题指定提交后可核对的数据</label>
          {question.post_cutoff_reference && <><p className="text-sm leading-6 text-muted">仅在“研究规则”开启后，对提交了本题的研究员开放此范围。</p><div className="grid gap-3 sm:grid-cols-3"><Field label="核对开始日"><Input type="date" max={data.latest_date} value={question.post_cutoff_reference.start_date} onChange={e => changeQuestion({ post_cutoff_reference: { ...question.post_cutoff_reference!, start_date: e.target.value } })} /></Field><Field label="核对结束日"><Input type="date" max={data.latest_date} value={question.post_cutoff_reference.end_date} onChange={e => changeQuestion({ post_cutoff_reference: { ...question.post_cutoff_reference!, end_date: e.target.value } })} /></Field><Field label="仅此商品（选填）"><Input value={question.post_cutoff_reference.product ?? ""} onChange={e => changeQuestion({ post_cutoff_reference: { ...question.post_cutoff_reference!, product: e.target.value || null } })} /></Field></div></>}
        </div></details>
        <Button variant="danger" disabled={busy} onClick={() => { setDraft({ ...draft, questions: draft.questions.filter((_, i) => i !== selected) }); setSelected(Math.max(0, selected - 1)); }}>从草稿移除此题</Button>
      </div> : <Empty>添加第一道研究题，保存后发布。</Empty>}
    </div>
    <details><summary className="cursor-pointer font-medium">已发布版本 · {data.versions.length}</summary><div className="mt-4 divide-y divide-line">{data.versions.map(v => <div className="flex flex-wrap items-center justify-between gap-3 py-4" key={v.id}><div><span className="font-medium">{v.name || "历史题库"}</span> {v.active && <Badge tone="ok">当前</Badge>}<p className="mt-1 text-sm text-muted">{v.question_count} 题 · T {v.cutoff_T} · N {v.threshold_N}</p></div><Button disabled={busy} onClick={() => { setDraft({ revision: draft.revision, name: `${v.name || "研究题库"}（修订）`, cutoff_T: v.cutoff_T!, threshold_N: v.threshold_N!, questions: v.questions }); setSelected(0); setMessage(""); }}>复制到草稿</Button></div>)}</div></details>
  </div>;
}

export function ResearchPolicyEditor() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["admin-settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<ResearchPolicy | null>(null);
  useEffect(() => { if (settings.data) setDraft(settings.data); }, [settings.data]);
  const save = useMutation({ mutationFn: () => api.savePolicy({ allow_post_cutoff_reference_after_submit: draft!.allow_post_cutoff_reference_after_submit, anonymous_ab_evaluation: draft!.anonymous_ab_evaluation, invitation_criteria: draft!.invitation_criteria, idea_fields: draft!.idea_fields }), onSuccess: data => { client.setQueryData(["admin-settings"], data); client.invalidateQueries({ queryKey: ["research-policy"] }); } });
  if (settings.isPending) return <Spinner />;
  if (settings.error) return <Notice>{errorText(settings.error)}</Notice>;
  if (!draft) return <Spinner />;
  return <form className="space-y-8" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
    <div><h2 className="text-xl font-semibold">研究规则</h2><p className="mt-2 text-sm text-muted">规则对新提交生效；已有测评和研究想法保留提交时的记录。</p></div>
    <section className="space-y-5 border-b border-line pb-7"><h3 className="font-semibold">测评方式</h3>
      <label className="flex items-start gap-3"><input className="mt-1" type="checkbox" checked={draft.allow_post_cutoff_reference_after_submit} onChange={e => setDraft({ ...draft, allow_post_cutoff_reference_after_submit: e.target.checked })} /><span>提交答案后开放本题核对数据<span className="mt-1 block text-sm text-muted">范围由题库中每道题指定，自由研究仍受 T 限制。关闭后立即收回此额外访问权限。</span></span></label>
      <label className="flex items-start gap-3"><input className="mt-1" type="checkbox" checked={draft.anonymous_ab_evaluation} onChange={e => setDraft({ ...draft, anonymous_ab_evaluation: e.target.checked })} /><span>A/B 匿名评分<span className="mt-1 block text-sm text-muted">新作答随机排序两份答案，分别评分后揭示来源。研究员可能辨认自己的答案。</span></span></label>
    </section>
    <section className="space-y-4 border-b border-line pb-7"><h3 className="font-semibold">邀请加入的参考标准</h3><Field label="审核标准（选填）"><Textarea rows={5} maxLength={4000} value={draft.invitation_criteria} onChange={e => setDraft({ ...draft, invitation_criteria: e.target.value })} placeholder="填写研究员提交和审核时可参照的标准…" /></Field><p className="text-sm text-muted">审核人据此作出判断，不会自动发送邀请。</p></section>
    <section className="space-y-5"><h3 className="font-semibold">研究想法表单</h3>{draft.idea_fields.map((field, index) => <div key={field.key} className="grid items-end gap-3 md:grid-cols-[1fr_1fr_auto]"><Field label={`字段 ${index + 1} 名称`}><Input required maxLength={80} value={field.label} onChange={e => setDraft({ ...draft, idea_fields: draft.idea_fields.map(f => f.key === field.key ? { ...f, label: e.target.value } : f) })} /></Field><Field label="填写提示（选填）"><Input maxLength={200} value={field.hint} onChange={e => setDraft({ ...draft, idea_fields: draft.idea_fields.map(f => f.key === field.key ? { ...f, hint: e.target.value } : f) })} /></Field><label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" disabled={field.key === "question"} checked={field.required} onChange={e => setDraft({ ...draft, idea_fields: draft.idea_fields.map(f => f.key === field.key ? { ...f, required: e.target.checked } : f) })} />必填</label></div>)}</section>
    <div className="flex flex-wrap items-center gap-4"><Button type="submit" variant="primary" disabled={save.isPending}>保存研究规则</Button>{save.isSuccess && <span className="text-sm text-ok">已保存</span>}{save.error && <Notice>{errorText(save.error)}</Notice>}</div>
  </form>;
}

export function ModelServiceEditor() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["admin-model-service"], queryFn: api.modelService });
  const [key, setKey] = useState("");
  const save = useMutation({ mutationFn: () => api.saveModelKey(key.trim()), onSuccess: data => {
    setKey(""); client.setQueryData(["admin-model-service"], data);
  } });
  const check = useMutation({ mutationFn: api.checkModelService });
  const disable = useMutation({ mutationFn: api.disableModelService, onSuccess: data => {
    setKey(""); save.reset(); check.reset(); client.setQueryData(["admin-model-service"], data);
  } });
  const busy = save.isPending || check.isPending || disable.isPending;
  if (settings.isPending) return <Spinner />;
  if (settings.error) return <Notice>{errorText(settings.error)} <Button variant="ghost" onClick={() => settings.refetch()}>重新加载</Button></Notice>;
  return <div className="max-w-2xl space-y-7">
    <div className="flex items-center justify-between gap-4 border-b border-line pb-5"><h2 className="text-xl font-semibold">模型服务</h2><Badge tone={settings.data.configured ? "ok" : "neutral"}>{settings.data.configured ? "已配置" : "未配置"}</Badge></div>
    <div><h3 className="font-medium">DeepSeek</h3><p className="mt-2 text-sm leading-6 text-muted">研究对话和测评共用此 API Key。模型和思考强度由使用者在提问时选择。</p></div>
    <form className="space-y-5" onSubmit={event => { event.preventDefault(); check.reset(); disable.reset(); save.mutate(); }}>
      <Field label={settings.data.configured ? "替换 API Key" : "API Key"} hint="保存前验证连接，成功后立即用于新任务。已保存的密钥不会回显。">
        <Input type="password" name="deepseek-api-key" autoComplete="off" autoCapitalize="none" spellCheck={false} required minLength={8} maxLength={256} disabled={busy} value={key}
          onChange={event => { setKey(event.target.value); save.reset(); check.reset(); }} placeholder={settings.data.configured ? "输入新的 DeepSeek API Key" : "输入 DeepSeek API Key"} />
      </Field>
      <div className="flex flex-wrap gap-3"><Button type="submit" variant="primary" disabled={busy || key.trim().length < 8}>{save.isPending ? "正在验证并保存…" : "验证并保存"}</Button>
        <Button disabled={busy || !settings.data.configured || !!key} onClick={() => { save.reset(); disable.reset(); check.mutate(); }}>{check.isPending ? "正在检查…" : "验证当前连接"}</Button></div>
    </form>
    {(save.error || check.error || disable.error) && <Notice>{errorText(save.error ?? check.error ?? disable.error)}</Notice>}
    {save.isSuccess && <Notice tone="ok">已保存，新的任务将使用此 API Key。</Notice>}
    {check.isSuccess && <Notice tone="ok">DeepSeek 连接验证通过。</Notice>}
    {disable.isSuccess && <Notice tone="neutral">模型服务已停用。保存新的 API Key 后可以继续提问。</Notice>}
    {settings.data.updated_at && <p className="text-sm text-muted">最近更新：{dateTime(settings.data.updated_at)}</p>}
    {settings.data.configured && <div className="border-t border-line pt-5"><Button variant="danger" disabled={busy} onClick={() => { if (confirm("停用模型服务后，将暂停新的研究对话和测评。是否停用？")) disable.mutate(); }}>停用模型服务</Button></div>}
  </div>;
}
