import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { Credentials, LoginResult, Me } from "../api/types";

const INPUT =
  "mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100 focus:border-accent focus:outline-none";

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  // Where to go after signing in, e.g. back to the setup wizard.
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const me = useQuery({ queryKey: queryKeys.me, queryFn: () => api.get<Me>("/auth/me") });
  const login = useMutation({
    mutationFn: (body: Credentials) => api.post<LoginResult>("/auth/login", body),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: queryKeys.me });
      navigate(from, { replace: true });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ username, password });
  };

  if (me.data && !login.isSuccess) return <Navigate to={from} replace />;

  let error: string | null = null;
  if (login.error) {
    error = login.error instanceof ApiError ? login.error.detail : "Could not reach Overlaparr";
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-lg border border-slate-700 bg-slate-800 p-6 shadow-xl"
      >
        <p className="text-sm font-semibold tracking-wide text-accent">Overlaparr</p>
        <h1 className="mt-1 text-xl font-semibold text-slate-100">Sign in</h1>
        {error && (
          <p
            role="alert"
            className="mt-4 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
          >
            {error}
          </p>
        )}
        <label className="mt-4 block text-sm text-slate-300">
          Username
          <input
            className={INPUT}
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="mt-3 block text-sm text-slate-300">
          Password
          <input
            className={INPUT}
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button
          type="submit"
          disabled={login.isPending}
          className="mt-5 w-full rounded bg-accent px-4 py-2 font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          Sign in
        </button>
      </form>
    </main>
  );
}
