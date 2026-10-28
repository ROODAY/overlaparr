import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { Dashboard } from "../api/types";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard" },
  { to: "/findings", label: "Findings" },
  { to: "/automation", label: "Automation" },
  { to: "/activity", label: "Activity log" },
  { to: "/servers", label: "Servers" },
  { to: "/instances", label: "Instances" },
  { to: "/notifications", label: "Notifications" },
  { to: "/settings", label: "Settings" },
  { to: "/logs", label: "Logs" },
] as const;

const POLL_MS = 30_000;

export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<Dashboard>("/dashboard"),
    refetchInterval: POLL_MS,
  });
  const configError = dashboard.data?.config_error;

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      // Already signed out or unreachable: go to the login page regardless.
    }
    // Drop everything cached for this session; setup status is not per-user.
    queryClient.removeQueries({
      predicate: (q) => q.queryKey[0] !== queryKeys.setupStatus[0],
    });
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-950 px-4">
        <button
          type="button"
          aria-label="Menu"
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          onClick={() => setMenuOpen((open) => !open)}
          className="rounded p-1.5 text-slate-300 hover:bg-slate-800 md:hidden"
        >
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5" aria-hidden="true">
            <path d="M3 5h14v2H3zm0 4h14v2H3zm0 4h14v2H3z" />
          </svg>
        </button>
        <span className="text-lg font-semibold text-slate-100">
          Overlap<span className="text-accent">arr</span>
        </span>
        <button
          type="button"
          onClick={logout}
          className="ml-auto rounded px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
        >
          Log out
        </button>
      </header>
      <div className="relative flex flex-1">
        {menuOpen && (
          <div
            data-testid="sidebar-backdrop"
            aria-hidden="true"
            onClick={() => setMenuOpen(false)}
            className="absolute inset-0 z-30 bg-black/50 md:hidden"
          />
        )}
        <aside
          id="sidebar"
          className={`${menuOpen ? "block" : "hidden"} absolute inset-y-0 left-0 z-40 w-56 border-r border-slate-800 bg-slate-950 md:static md:block`}
        >
          <nav aria-label="Main" className="flex flex-col gap-0.5 p-2">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                onClick={() => setMenuOpen(false)}
                className={({ isActive }) =>
                  `rounded px-3 py-2 text-sm ${
                    isActive
                      ? "bg-accent/15 font-medium text-accent"
                      : "text-slate-300 hover:bg-slate-800 hover:text-slate-100"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="min-w-0 flex-1 p-4 md:p-6">
          {configError && (
            <div
              role="alert"
              className="mb-4 rounded border border-red-500/50 bg-red-500/10 px-4 py-3 text-sm text-red-200"
            >
              <p className="font-semibold text-red-300">config.yaml is invalid</p>
              <p className="mt-1">
                Overlaparr keeps running on the last valid configuration and can't save changes
                until the file is fixed.
              </p>
              <pre className="mt-2 font-mono text-xs whitespace-pre-wrap">{configError}</pre>
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  );
}
