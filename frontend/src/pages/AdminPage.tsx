import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RATINGS_EXPORT_URL, api } from "../api/client";
import type { AdminUser, InviteLink, NewUser, Permission, Role, SiteSettings } from "../api/types";
import { Page } from "../components/Layout";
import { Badge, Button, Card, Empty, Field, Input, Notice, PageHeader, Select, Spinner } from "../components/ui";
import { can, ME_KEY, useMe } from "../lib/auth";
import { dateTime, errorText, integer, relative, thisMonth, yuan } from "../lib/format";
import { RatingCard } from "./RatingsPage";
import { AssignmentsTab, FeedbackQueue, IdeasQueue } from "./ReviewPages";
import { BankManager, ModelServiceEditor, ResearchPolicyEditor } from "./AdminConfigPages";

const TABS = [
  { id: "bank", label: "题库管理", group: "工作台配置" },
  { id: "policy", label: "研究规则", group: "工作台配置" },
  { id: "model", label: "模型服务", group: "工作台配置" },
  { id: "users", label: "成员与权限", group: "工作台配置" },
  { id: "settings", label: "额度与运行", group: "工作台配置" },
  { id: "assignments", label: "派题与进度", group: "研究协作" },
  { id: "feedback", label: "问题复核", group: "研究协作" },
  { id: "ideas", label: "研究想法", group: "研究协作" },
  { id: "researchers", label: "研究员档案", group: "研究协作" },
  { id: "ratings", label: "测评记录", group: "记录与用量" },
  { id: "usage", label: "用量与费用", group: "记录与用量" },
] as const;
const PERMISSIONS: [Permission, string][] = [["review_feedback", "复核问题反馈"], ["review_ideas", "审核研究想法"], ["view_researchers", "查看研究员档案与对话"]];

function InviteBox({ link, onClose }: { link: InviteLink; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Notice tone="ok">
      <div className="mb-1 font-medium">邀请链接已生成（只显示这一次，{dateTime(link.expires_at)} 前有效）</div>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 rounded bg-surface px-2 py-1 break-all text-ink">{link.invite_url}</code>
        <Button className="h-7 text-xs" onClick={() => navigator.clipboard?.writeText(link.invite_url).then(() => setCopied(true))}>{copied ? "已复制" : "复制"}</Button>
        <Button variant="ghost" className="h-7 text-xs" onClick={onClose}>关闭</Button>
      </div>
    </Notice>
  );
}

function UserRow({ user, self, onInvite }: { user: AdminUser; self: boolean; onInvite: (link: InviteLink) => void }) {
  const client = useQueryClient();
  const [limit, setLimit] = useState(String(user.daily_limit));
  const [reserve, setReserve] = useState(String(user.evaluation_reserve));
  const update = useMutation({
    mutationFn: (patch: Parameters<typeof api.updateUser>[1]) => api.updateUser(user.id, patch),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["admin-users"] }); client.invalidateQueries({ queryKey: ME_KEY }); },
  });
  const reinvite = useMutation({ mutationFn: () => api.reinvite(user.id), onSuccess: onInvite });
  const dirty = Number(limit) !== user.daily_limit || Number(reserve) !== user.evaluation_reserve;

  return (
    <tr className={`border-t border-[#eef0e8] align-middle ${user.disabled ? "text-faint" : ""}`}>
      <td className="px-3 py-2.5">
        <Link to={`/admin/researchers/${user.id}`} className="font-medium underline">{user.display_name}</Link>
        <div className="text-xs text-muted">{user.username}</div>
      </td>
      <td className="px-3 py-2.5">
        <Select aria-label={`${user.display_name}的角色`} className="h-8 min-w-24 text-xs" value={user.role} disabled={self || update.isPending}
                onValueChange={(e) => update.mutate({ role: e as Role })}>
          <option value="researcher">研究员</option>
          <option value="admin">管理员</option>
        </Select>
        {user.role !== "admin" && <details className="mt-2 whitespace-normal"><summary className="cursor-pointer text-sm text-muted">审核权限 · {user.permissions.length}</summary><div className="mt-3 space-y-3">{PERMISSIONS.map(([key, label]) => <label key={key} className="flex items-start gap-2 text-sm"><input type="checkbox" disabled={update.isPending} checked={user.permissions.includes(key)} onChange={e => update.mutate({ permissions: e.target.checked ? [...user.permissions, key] : user.permissions.filter(p => p !== key) })} />{label}</label>)}</div></details>}
      </td>
      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <Input aria-label={`${user.display_name}的每日上限`} className="h-8 min-w-16 max-w-20 text-xs" type="number" min={0} max={500} value={limit} onChange={(e) => setLimit(e.target.value)} />
        </div>
      </td>
      <td className="px-3 py-2.5"><div className="flex items-center gap-2"><Input aria-label={`${user.display_name}的测评预留`} className="h-8 min-w-16 max-w-20 text-xs" type="number" min={0} max={Number(limit)} value={reserve} onChange={e => setReserve(e.target.value)} />{dirty && <Button className="h-8 px-2 text-xs" disabled={update.isPending || !limit || !reserve || Number(reserve) > Number(limit)} onClick={() => update.mutate({ daily_limit: Number(limit), evaluation_reserve: Number(reserve) })}>保存</Button>}</div></td>
      <td className="px-3 py-2.5 text-xs tabular-nums">{user.used_today}</td>
      <td className="px-3 py-2.5 text-xs">{user.invite_pending ? <Badge tone="warn">待接受邀请</Badge> : user.disabled ? <Badge>已停用</Badge> : <Badge tone="ok">正常</Badge>}</td>
      <td className="px-3 py-2.5 text-xs text-muted">{relative(user.last_active_at)}</td>
      <td className="px-3 py-2.5">
        <div className="flex gap-1.5">
          {!self && <Button variant="ghost" className="h-8 px-2 text-xs" disabled={reinvite.isPending}
                  onClick={() => { if (user.invite_pending || confirm(`重置 ${user.display_name} 的密码并生成新邀请链接？`)) reinvite.mutate(); }}>
            {user.invite_pending ? "重发邀请" : "重置密码"}
          </Button>}
          {!self && (
            <Button variant={user.disabled ? "secondary" : "danger"} className="h-8 px-2 text-xs" disabled={update.isPending}
                    onClick={() => update.mutate({ disabled: !user.disabled })}>{user.disabled ? "启用" : "停用"}</Button>
          )}
        </div>
        {(update.error || reinvite.error) && <div className="mt-2 text-sm text-warn">{errorText(update.error ?? reinvite.error)}</div>}
      </td>
    </tr>
  );
}

function UsersTab() {
  const me = useMe().data!;
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: api.users });
  const settings = useQuery({ queryKey: ["admin-settings"], queryFn: api.settings });
  const [form, setForm] = useState<NewUser>({ username: "", display_name: "", role: "researcher" });
  const [invite, setInvite] = useState<InviteLink | null>(null);
  const create = useMutation({
    mutationFn: () => api.createUser(form),
    onSuccess: (result) => {
      setInvite(result);
      setForm({ username: "", display_name: "", role: "researcher" });
      client.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };

  return (
    <div className="space-y-4">
      {invite && <InviteBox link={invite} onClose={() => setInvite(null)} />}
      <Card className="p-4">
        <form onSubmit={submit} className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Field label="用户名"><Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} placeholder="小写字母或数字" required /></Field>
          <Field label="显示名"><Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} required /></Field>
          <Field label="角色">
            <Select value={form.role} onValueChange={(e) => setForm({ ...form, role: e as Role })}>
              <option value="researcher">研究员</option><option value="admin">管理员</option>
            </Select>
          </Field>
          <Field label="每日提问上限"><Input type="number" min={0} max={500} placeholder={String(settings.data?.default_daily_limit ?? 30)} value={form.daily_limit ?? ""} onChange={(e) => setForm({ ...form, daily_limit: e.target.value === "" ? undefined : Number(e.target.value) })} /></Field>
          <Field label="测评预留"><Input type="number" min={0} max={form.daily_limit ?? settings.data?.default_daily_limit ?? 500} placeholder={String(settings.data?.default_evaluation_reserve ?? 0)} value={form.evaluation_reserve ?? ""} onChange={e => setForm({ ...form, evaluation_reserve: e.target.value === "" ? undefined : Number(e.target.value) })} /></Field>
          <div className="col-span-2 flex items-end md:col-span-1"><Button type="submit" variant="primary" className="w-full" disabled={create.isPending}>创建并生成邀请</Button></div>
        </form>
        {create.error && <div className="mt-3"><Notice>{errorText(create.error)}</Notice></div>}
      </Card>
      {users.isPending ? <Spinner /> : users.error ? <Notice>{errorText(users.error)}</Notice> : (
        <Card className="overflow-x-auto">
          <table className="min-w-full text-left text-[13px] whitespace-nowrap">
            <thead className="bg-[#f0f2e9] text-xs text-[#6e785f]">
              <tr>{["成员", "角色", "每日上限", "测评预留", "今日已用", "状态", "最近活动", "操作"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>{users.data.map((u) => <UserRow key={u.id} user={u} self={u.id === me.id} onInvite={setInvite} />)}</tbody>
          </table>
          <p className="px-4 py-3 text-sm text-muted">{users.data.length} / {settings.data?.max_accounts ?? "—"} 个账号</p>
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card className="p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-1.5 text-xl font-medium tabular-nums">{value}</div>
      {sub && <div className="mt-2 text-xs text-muted">{sub}</div>}
    </Card>
  );
}

function UsageTab() {
  const [month, setMonth] = useState(thisMonth());
  const usage = useQuery({ queryKey: ["admin-usage", month], queryFn: () => api.usage(month) });
  const settings = useQuery({ queryKey: ["admin-settings"], queryFn: api.settings });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="月份"><Input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></Field>
        {settings.data && <details className="pb-2 text-xs text-muted"><summary className="underline">费用计算标准</summary><p className="mt-2 leading-6">每百万 token：输入 {yuan(settings.data.input_price_per_million_yuan)} 元，输出 {yuan(settings.data.output_price_per_million_yuan)} 元。</p></details>}
      </div>
      {usage.isPending ? <Spinner /> : usage.error ? <Notice>{errorText(usage.error)}</Notice> : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="提问次数" value={integer(usage.data.total.questions)} />
            <Stat label="输入 token" value={integer(usage.data.total.input_tokens)} />
            <Stat label="输出 token" value={integer(usage.data.total.output_tokens)} />
            <Stat label="估算费用（元）" value={yuan(usage.data.total.cost_yuan)} sub="按当前单价估算，以账单为准" />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="overflow-x-auto">
              <div className="px-4 pt-3 text-xs font-medium">按成员</div>
              <table className="mt-2 min-w-full text-xs whitespace-nowrap">
                <thead className="text-[#6e785f]"><tr>{["成员", "提问", "输入 token", "输出 token", "费用(元)"].map((h, i) => <th key={h} className={`px-4 py-2 font-medium ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
                <tbody>
                  {usage.data.by_user.map((u) => (
                    <tr key={u.user_id} className="border-t border-[#eef0e8]">
                      <td className="px-4 py-2">{u.display_name}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{integer(u.questions)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{integer(u.input_tokens)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{integer(u.output_tokens)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{yuan(u.cost_yuan)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <Card className="overflow-x-auto">
              <div className="px-4 pt-3 text-xs font-medium">按日</div>
              {usage.data.by_day.length === 0 ? <Empty>这个月还没有用量。</Empty> : (
                <div className="max-h-96 overflow-y-auto">
                  <table className="mt-2 min-w-full text-xs whitespace-nowrap">
                    <thead className="text-[#6e785f]"><tr>{["日期", "提问", "token 合计", "费用(元)"].map((h, i) => <th key={h} className={`px-4 py-2 font-medium ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
                    <tbody>
                      {usage.data.by_day.map((d) => (
                        <tr key={d.date} className="border-t border-[#eef0e8]">
                          <td className="px-4 py-2">{d.date}</td>
                          <td className="px-4 py-2 text-right tabular-nums">{integer(d.questions)}</td>
                          <td className="px-4 py-2 text-right tabular-nums">{integer(d.input_tokens !== null && d.output_tokens !== null ? d.input_tokens + d.output_tokens : null)}</td>
                          <td className="px-4 py-2 text-right tabular-nums">{yuan(d.cost_yuan)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function RatingsTab() {
  const ratings = useQuery({ queryKey: ["ratings", "all"], queryFn: api.allRatings });
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted">全部成员</span>
        <a href={RATINGS_EXPORT_URL} className="button-link" download>导出 CSV</a>
      </div>
      {ratings.isPending ? <Spinner /> : ratings.error ? <Notice>{errorText(ratings.error)}</Notice>
        : ratings.data.length === 0 ? <Empty>还没有测评记录。</Empty>
        : ratings.data.map((r) => <RatingCard key={r.id} rating={r} showUser />)}
    </div>
  );
}

const SETTING_FIELDS: { key: keyof SiteSettings; label: string; hint: string; type: "number" | "date"; step?: string }[] = [
  { key: "max_accounts", label: "账号数量上限", hint: "包括管理员、审核人和研究员", type: "number" },
  { key: "default_daily_limit", label: "新成员每日提问上限", hint: "不改变已有成员额度", type: "number" },
  { key: "default_evaluation_reserve", label: "新成员测评预留", hint: "包含在每日上限内", type: "number" },
  { key: "site_daily_limit", label: "全站每日提问上限", hint: "", type: "number" },
  { key: "max_concurrent_jobs", label: "全站最多同时运行", hint: "超出的问题排队", type: "number" },
  { key: "per_user_concurrent_jobs", label: "每人最多同时运行", hint: "", type: "number" },
  { key: "input_price_per_million_yuan", label: "默认模型输入单价（元/百万 token）", hint: "其他模型费用显示未知", type: "number", step: "0.01" },
  { key: "output_price_per_million_yuan", label: "默认模型输出单价（元/百万 token）", hint: "只用于费用估算", type: "number", step: "0.01" },
];

function SettingsTab() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["admin-settings"], queryFn: api.settings });
  const [draft, setDraft] = useState<SiteSettings | null>(null);
  useEffect(() => { if (settings.data) setDraft(settings.data); }, [settings.data]);
  const save = useMutation({
    mutationFn: () => api.saveRuntime(draft!),
    onSuccess: (saved) => {
      client.setQueryData(["admin-settings"], saved);
      client.invalidateQueries({ queryKey: ["status"] });
      client.invalidateQueries({ queryKey: ME_KEY });
    },
  });
  if (settings.isPending || !draft) return <Spinner />;
  if (settings.error) return <Notice>{errorText(settings.error)}</Notice>;
  return (
    <Card className="p-5">
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="grid gap-4 md:grid-cols-2">
        {SETTING_FIELDS.map((f) => (
          <Field key={f.key} label={f.label} hint={f.hint}>
            <Input type={f.type} step={f.step} value={draft[f.key] === null ? "" : String(draft[f.key])} readOnly={f.key === "per_user_concurrent_jobs"}
                   onChange={(e) => setDraft({ ...draft, [f.key]: f.type === "number" ? (e.target.value === "" && f.key.includes("price") ? null : Number(e.target.value)) : e.target.value })} />
          </Field>
        ))}
        <div className="flex items-center gap-3 md:col-span-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>保存设置</Button>
          {save.isSuccess && <span className="text-xs text-ok">已保存</span>}
          {save.error && <span className="text-xs text-warn">{errorText(save.error)}</span>}
        </div>
      </form>
    </Card>
  );
}

export function AdminPage() {
  const me = useMe().data!;
  const [params, setParams] = useSearchParams();
  const available = TABS.filter(t => me.role === "admin" || (t.id === "feedback" && can(me, "review_feedback")) || (t.id === "ideas" && can(me, "review_ideas")) || (t.id === "researchers" && can(me, "view_researchers")));
  const tab = available.find((t) => t.id === params.get("tab"))?.id ?? available[0]?.id;
  return (
    <Page wide>
      <PageHeader title={me.role === "admin" ? "管理后台" : "审核工作台"} />
      <div className="grid min-w-0 gap-7 lg:grid-cols-[160px_minmax(0,1fr)]">
      <nav aria-label="管理模块" className="flex flex-wrap content-start gap-2 lg:block lg:space-y-7">{["工作台配置", "研究协作", "记录与用量"].map(group => available.some(t => t.group === group) && <div key={group}><p className="mb-2 hidden text-sm font-medium text-muted lg:block">{group}</p><div className="flex flex-wrap gap-1 lg:block lg:space-y-1">{available.filter(t => t.group === group).map(t => <button key={t.id} type="button" aria-current={tab === t.id ? "page" : undefined} onClick={() => setParams({ tab: t.id })} className={`rounded-lg px-3 py-2.5 text-left text-sm lg:w-full ${tab === t.id ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-sidebar hover:text-ink"}`}>{t.label}</button>)}</div></div>)}</nav>
      <div className="min-w-0" key={tab}>
      {tab === "bank" && <BankManager />}
      {tab === "policy" && <ResearchPolicyEditor />}
      {tab === "model" && <ModelServiceEditor />}
      {tab === "users" && <UsersTab />}
      {tab === "usage" && <UsageTab />}
      {tab === "ratings" && <RatingsTab />}
      {tab === "settings" && <SettingsTab />}
      {tab === "assignments" && <AssignmentsTab />}
      {tab === "feedback" && <FeedbackQueue />}
      {tab === "ideas" && <IdeasQueue />}
      {tab === "researchers" && <ResearcherDirectory />}
      </div></div>
    </Page>
  );
}

function ResearcherDirectory() {
  const members = useQuery({ queryKey: ["review-members"], queryFn: api.reviewMembers });
  return members.isPending ? <Spinner /> : members.error ? <Notice>{errorText(members.error)}</Notice> : <div><h2 className="mb-5 text-xl font-semibold">研究员档案</h2><div className="divide-y divide-line">{members.data.map(m => <Link className="flex items-center justify-between py-4 underline underline-offset-4" key={m.id} to={`/admin/researchers/${m.id}`}>{m.display_name}<span className="text-sm text-muted">查看研究与反馈</span></Link>)}</div></div>;
}
