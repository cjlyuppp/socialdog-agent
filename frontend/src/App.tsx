import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router";
import { onSessionExpired } from "./api/client";
import { Layout } from "./components/Layout";
import { Notice, Spinner } from "./components/ui";
import { ME_KEY, useMe } from "./lib/auth";
import { errorText } from "./lib/format";
import { AdminPage } from "./pages/AdminPage";
import { ChatPage } from "./pages/ChatPage";
import { EvaluatePage } from "./pages/EvaluatePage";
import { InvitePage, LoginPage } from "./pages/AuthPages";
import { RatingsPage } from "./pages/RatingsPage";
import { ReferencePage } from "./pages/ReferencePage";

export default function App() {
  const client = useQueryClient();
  const { data: me, isPending, error } = useMe();
  const location = useLocation();
  useEffect(() => onSessionExpired(() => {
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] });
    client.setQueryData(ME_KEY, null);
  }), [client]);
  // After signing in, return to the page that sent the user to /login.
  const from = (location.state as { from?: string } | null)?.from;

  if (isPending) return <Spinner label="正在检查登录状态" />;
  if (error) return <div className="mx-auto max-w-md p-8"><Notice>{errorText(error)}</Notice></div>;

  return (
    <Routes>
      <Route path="/login" element={me ? <Navigate to={from && from !== "/login" ? from : "/chat"} replace /> : <LoginPage />} />
      <Route path="/invite/:token" element={<InvitePage />} />
      {me ? (
        <Route element={<Layout me={me} />}>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:sessionId" element={<ChatPage />} />
          <Route path="/reference" element={<ReferencePage />} />
          <Route path="/evaluate" element={<EvaluatePage />} />
          <Route path="/evaluate/:attemptId" element={<EvaluatePage />} />
          <Route path="/ratings" element={<RatingsPage />} />
          <Route path="/admin" element={me.role === "admin" ? <AdminPage /> : <Navigate to="/chat" replace />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      ) : (
        <Route path="*" element={<Navigate to="/login" replace state={{ from: location.pathname }} />} />
      )}
    </Routes>
  );
}
