import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Brand } from "../components/Layout";
import { Button, Field, Input, Notice, Spinner } from "../components/ui";
import { useSignedIn } from "../lib/auth";
import { dateTime, errorText } from "../lib/format";

function AuthFrame({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="auth-page">
      <Brand />
      <div className="auth-content">
        <h1>{title}</h1>
        {subtitle && <p className="auth-subtitle">{subtitle}</p>}
        {children}
      </div>
    </div>
  );
}

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const signedIn = useSignedIn();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      // App redirects away from /login once the user is known.
      signedIn(await api.login(username.trim(), password));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame title="欢迎回来">
      <form onSubmit={submit} className="mt-8 space-y-5">
        <Field label="用户名"><Input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></Field>
        <Field label="密码"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <Notice>{error}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={busy}>{busy ? "正在登录…" : "登录"}</Button>
      </form>
      <details className="auth-help"><summary>还没有账号？</summary><p>联系管理员获取邀请链接。</p></details>
      {import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== "0" && <details className="auth-help"><summary>演示账号</summary><p>管理员：carrey / demo<br />研究员：lin / demo</p></details>}
    </AuthFrame>
  );
}

export function InvitePage() {
  const { token = "" } = useParams();
  const invite = useQuery({ queryKey: ["invite", token], queryFn: () => api.invite(token) });
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const signedIn = useSignedIn();
  const navigate = useNavigate();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < 10) return setError("密码至少 10 位。");
    if (password !== confirm) return setError("两次输入的密码不一致。");
    setBusy(true);
    setError("");
    try {
      signedIn(await api.acceptInvite(token, password));
      navigate("/chat", { replace: true });
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  if (invite.isPending) return <Spinner label="正在核对邀请链接" />;
  if (invite.error) {
    return <AuthFrame title="邀请链接不可用" subtitle="请联系管理员重新发送邀请。"><Notice>{errorText(invite.error)}</Notice></AuthFrame>;
  }
  return (
    <AuthFrame title={`欢迎，${invite.data.display_name}`}
               subtitle={<>为账号 <strong className="text-ink">{invite.data.username}</strong> 设置密码。链接有效期至 {dateTime(invite.data.expires_at)}。</>}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="新密码" hint="至少 10 位"><Input type="password" minLength={10} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus /></Field>
        <Field label="再次输入"><Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></Field>
        {error && <Notice>{error}</Notice>}
        <Button type="submit" variant="primary" className="w-full" disabled={busy}>{busy ? "正在保存…" : "设置密码并进入"}</Button>
      </form>
    </AuthFrame>
  );
}
