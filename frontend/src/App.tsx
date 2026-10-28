import { QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { api, ApiError, setUnauthorizedHandler } from "./api/client";
import { createQueryClient, queryKeys } from "./api/queryClient";
import type { Me, SetupStatus } from "./api/types";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { Dashboard } from "./pages/Dashboard";
import { FindingDetail } from "./pages/FindingDetail";
import { Findings } from "./pages/Findings";
import { Login } from "./pages/Login";
import { Placeholder } from "./pages/Placeholder";
import { Setup } from "./pages/Setup";

export default function App() {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

function Message({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-4 text-slate-400">
      {children}
    </div>
  );
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
}

/** An expired session anywhere sends the user to /login (no page reload). */
function useUnauthorizedRedirect() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.removeQueries({ queryKey: queryKeys.me });
      navigate("/login", { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, queryClient]);
}

function AppRoutes() {
  useUnauthorizedRedirect();
  const location = useLocation();
  const status = useQuery({
    queryKey: queryKeys.setupStatus,
    queryFn: () => api.get<SetupStatus>("/setup/status"),
  });

  if (status.isPending) return <Message>Loading…</Message>;
  if (status.isError) return <Message>{errorText(status.error)}</Message>;
  if (status.data.needs_user && location.pathname !== "/setup") {
    return <Navigate to="/setup" replace />;
  }

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/setup" element={<Setup />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="findings" element={<Findings />} />
          <Route path="findings/:id" element={<FindingDetail />} />
          <Route path="automation" element={<Placeholder name="Automation" />} />
          <Route path="automation/:id" element={<Placeholder name="Automation" />} />
          <Route path="activity" element={<Placeholder name="Activity log" />} />
          <Route path="servers" element={<Placeholder name="Servers" />} />
          <Route path="instances" element={<Placeholder name="Instances" />} />
          <Route path="notifications" element={<Placeholder name="Notifications" />} />
          <Route path="settings" element={<Placeholder name="Settings" />} />
          <Route path="logs" element={<Placeholder name="Logs" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}

function RequireAuth() {
  const me = useQuery({ queryKey: queryKeys.me, queryFn: () => api.get<Me>("/auth/me") });
  if (me.isPending) return <Message>Loading…</Message>;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate to="/login" replace />;
    }
    return <Message>{errorText(me.error)}</Message>;
  }
  return <Outlet />;
}
