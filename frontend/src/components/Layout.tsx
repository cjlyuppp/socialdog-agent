import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Me } from "../api/types";
import { relative } from "../lib/format";
import { Icon, type IconName } from "./Icon";

const LINKS: { to: string; label: string; icon: IconName }[] = [
  { to: "/chat", label: "研究对话", icon: "chat" },
  { to: "/reference", label: "数据核对", icon: "data" },
  { to: "/evaluate", label: "测评", icon: "evaluate" },
  { to: "/ratings", label: "我的测评", icon: "history" },
];

export function Brand() {
  return <span className="brand"><span className="brand-mark">S<span>·</span></span>SocialDog</span>;
}

function Sidebar({ me, onNavigate }: { me: Me; onNavigate: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [logoutError, setLogoutError] = useState(false);
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions, enabled: location.pathname.startsWith("/chat") });

  async function logout() {
    try {
      await api.logout();
      client.clear();
      onNavigate();
      navigate("/login");
    } catch {
      setLogoutError(true);
    }
  }

  return (
    <div className="sidebar-content">
      <NavLink to="/chat" className="sidebar-brand" aria-label="SocialDog 首页" onClick={onNavigate}><Brand /></NavLink>
      <nav className="workspace-nav" aria-label="主导航">
        {LINKS.map((link) => <NavLink key={link.to} to={link.to} onClick={onNavigate} className={({ isActive }) => `nav-item ${isActive ? "is-active" : ""}`}><Icon name={link.icon} /><span>{link.label}</span></NavLink>)}
      </nav>
      <div className="sidebar-history">
        {location.pathname.startsWith("/chat") && <>
          <div className="history-heading"><span>最近对话</span><button type="button" className="icon-button" aria-label="新建对话" onClick={() => { navigate("/chat"); onNavigate(); }}><Icon name="plus" size={18} /></button></div>
          {sessions.isError && <button className="history-retry" onClick={() => sessions.refetch()}>对话加载失败，重试</button>}
          <nav aria-label="研究会话" className="session-list">
            {sessions.data?.map((session) => <NavLink key={session.id} to={`/chat/${session.id}`} onClick={onNavigate} title={session.title} className={({ isActive }) => `session-link ${isActive ? "is-active" : ""}`}><span>{session.title}</span><time>{relative(session.updated_at)}</time></NavLink>)}
          </nav>
        </>}
      </div>
      <div className="sidebar-bottom">
        {me.role === "admin" && <NavLink to="/admin" onClick={onNavigate} className={({ isActive }) => `nav-item ${isActive ? "is-active" : ""}`}><Icon name="settings" /><span>管理后台</span></NavLink>}
        <div className="account-row"><span className="account-avatar">{me.display_name.slice(0, 1).toUpperCase()}</span><div><strong>{me.display_name}</strong><span>今日剩余 {me.quota.remaining_today} 次</span></div><button type="button" className="icon-button" aria-label="退出登录" onClick={logout}><Icon name="logout" size={18} /></button></div>
        {import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== "0" && <span className="mock-indicator">演示环境</span>}
        {logoutError && <p role="alert" className="text-sm text-warn">退出失败，请重试。</p>}
      </div>
    </div>
  );
}

export function Layout({ me }: { me: Me }) {
  const menu = useRef<HTMLDialogElement>(null);
  const main = useRef<HTMLElement>(null);
  const location = useLocation();
  const closeMenu = () => menu.current?.close();
  useEffect(() => { main.current?.scrollTo({ top: 0 }); }, [location.pathname, location.search]);
  return (
    <div className="app-shell">
      <aside className="desktop-sidebar"><Sidebar me={me} onNavigate={closeMenu} /></aside>
      <header className="mobile-header"><Brand /><button type="button" className="icon-button" aria-label="打开菜单" onClick={() => menu.current?.showModal()}><Icon name="menu" /></button></header>
      <dialog ref={menu} className="mobile-menu" aria-label="导航菜单" onClick={(event) => { if (event.target === event.currentTarget) closeMenu(); }}>
        <button type="button" className="menu-close icon-button" aria-label="关闭菜单" onClick={closeMenu}><Icon name="close" /></button>
        <Sidebar me={me} onNavigate={closeMenu} />
      </dialog>
      <main ref={main} className="app-main"><Outlet /></main>
    </div>
  );
}

export function Page({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return <div className={`page-body ${wide ? "page-wide" : ""}`}>{children}</div>;
}
