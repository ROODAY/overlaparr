import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { HomeResult, ServerRow, ServerStatus } from "../api/types";
import { Badge, type BadgeVariant } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Switch } from "../components/Switch";
import { useToast } from "../components/useToast";
import { formatDateTime, formatRelative } from "../lib/format";

const STATUS_VARIANT: Record<ServerStatus, BadgeVariant> = {
  online: "good",
  degraded: "caution",
  partial: "caution",
  offline: "bad",
  stale: "bad",
  disabled: "neutral",
};

const INPUT =
  "w-40 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-slate-100 focus:border-accent focus:outline-none";
const SMALL_BUTTON = "rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:opacity-50";

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : "could not reach Overlaparr";
}

export function Servers() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const servers = useQuery({
    queryKey: queryKeys.servers,
    queryFn: () => api.get<ServerRow[]>("/servers"),
  });
  const [editing, setEditing] = useState<{ id: string; value: string } | null>(null);
  const [pendingHome, setPendingHome] = useState<ServerRow | null>(null);

  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.patch<ServerRow>(`/servers/${id}`, { enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.servers }),
    onError: (e) => toast(`Could not update the server: ${message(e)}`, "error"),
  });

  const saveLabel = useMutation({
    mutationFn: ({ id, label }: { id: string; label: string | null }) =>
      api.patch<ServerRow>(`/servers/${id}`, { label }),
    onSuccess: () => {
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers });
    },
    onError: (e) => toast(`Could not rename the server: ${message(e)}`, "error"),
  });

  const refresh = useMutation({
    mutationFn: () => api.post<ServerRow[]>("/servers/refresh"),
    onSuccess: (rows) => {
      queryClient.setQueryData(queryKeys.servers, rows);
      toast("Servers refreshed");
    },
    onError: (e) => toast(`Could not refresh servers: ${message(e)}`, "error"),
  });

  const setHome = useMutation({
    mutationFn: (machineId: string) =>
      api.put<HomeResult>("/servers/home", { machine_id: machineId }),
    onSuccess: (result) => {
      for (const queryKey of [
        queryKeys.servers,
        queryKeys.dashboard,
        queryKeys.findings,
        queryKeys.scanStatus,
      ]) {
        void queryClient.invalidateQueries({ queryKey });
      }
      toast(
        result.scan_id === null
          ? "Home server changed."
          : "Home server changed — a scan is rebuilding findings.",
      );
      setPendingHome(null);
    },
    onError: (e) => {
      toast(`Could not change the home server: ${message(e)}`, "error");
      setPendingHome(null);
    },
  });

  const label = (s: ServerRow) => s.label || s.name;

  // A server never contacted has only the default status; don't claim it is offline.
  const statusBadge = (s: ServerRow) =>
    s.status !== "disabled" && s.last_attempt_at == null ? (
      <Badge variant="neutral">not checked</Badge>
    ) : (
      <Badge variant={STATUS_VARIANT[s.status]}>{s.status}</Badge>
    );

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">Servers</h1>
          <p className="mt-1 text-sm text-slate-400">
            The Plex servers shared with your account. Overlaparr looks for your library on these
            servers.
          </p>
        </div>
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {servers.isPending ? (
        <p className="mt-6 text-slate-400">Loading…</p>
      ) : servers.isError ? (
        <div className="mt-6">
          <p role="alert" className="text-red-300">
            {servers.error instanceof ApiError
              ? servers.error.detail
              : "Could not load servers"}
          </p>
          <button
            type="button"
            onClick={() => void servers.refetch()}
            className="mt-2 rounded border border-slate-600 px-3 py-1 text-sm text-slate-200 hover:bg-slate-700"
          >
            Retry
          </button>
        </div>
      ) : servers.data.length === 0 ? (
        <p className="mt-6 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
          No Plex servers yet. Use Refresh to discover the servers shared with your Plex account.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2">Server</th>
                <th className="px-3 py-2">Enabled</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Last indexed</th>
                <th className="px-3 py-2 text-right">Movies</th>
                <th className="px-3 py-2 text-right">Shows</th>
                <th className="px-3 py-2 text-right">Unmatchable</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/60">
              {servers.data.map((s) => (
                <tr key={s.machine_id} className="align-middle">
                  <td className="px-3 py-2">
                    {editing?.id === s.machine_id ? (
                      <form
                        className="flex flex-wrap items-center gap-1"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const value = editing.value.trim();
                          saveLabel.mutate({ id: s.machine_id, label: value || null });
                        }}
                      >
                        <input
                          autoFocus
                          aria-label={`Label for ${s.name}`}
                          value={editing.value}
                          onChange={(e) => setEditing({ id: s.machine_id, value: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") setEditing(null);
                          }}
                          className={INPUT}
                        />
                        <button
                          type="submit"
                          disabled={saveLabel.isPending}
                          className="rounded bg-accent px-2 py-1 text-white hover:bg-accent-hover disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditing(null)}
                          className={SMALL_BUTTON}
                        >
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-slate-100">{label(s)}</span>
                        {s.is_home && <Badge>Home</Badge>}
                        {s.is_new && <Badge variant="good">New</Badge>}
                        {s.label && s.label !== s.name && (
                          <span className="text-xs text-slate-400">({s.name})</span>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {/* the home server is never indexed, so it can't be switched off */}
                    {!s.is_home && (
                      <Switch
                        label={`Enable ${label(s)}`}
                        checked={s.enabled}
                        disabled={setEnabled.isPending}
                        onChange={(enabled) => setEnabled.mutate({ id: s.machine_id, enabled })}
                      />
                    )}
                  </td>
                  <td className="px-3 py-2">{statusBadge(s)}</td>
                  {/* the home row's index data is left over from when it was a friend's server */}
                  <td className="px-3 py-2 text-slate-300">
                    {s.is_home ? (
                      "—"
                    ) : s.last_indexed_at ? (
                      <time dateTime={s.last_indexed_at} title={formatDateTime(s.last_indexed_at)}>
                        {formatRelative(s.last_indexed_at)}
                      </time>
                    ) : (
                      "Never"
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-300">
                    {s.is_home ? "—" : s.movie_count}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-300">
                    {s.is_home ? "—" : s.show_count}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-300">
                    {s.is_home ? "—" : s.unmatchable_count}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => setEditing({ id: s.machine_id, value: label(s) })}
                        className={SMALL_BUTTON}
                      >
                        Edit label
                      </button>
                      {!s.is_home && (
                        <button
                          type="button"
                          onClick={() => setPendingHome(s)}
                          className={SMALL_BUTTON}
                        >
                          Make home
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pendingHome && (
        <ConfirmDialog
          open
          title="Change the home server?"
          body={
            <>
              Making <span className="font-semibold text-slate-100">{label(pendingHome)}</span> the
              home server deletes all findings and rebuilds them with a new scan. This can take a
              while.
            </>
          }
          confirmLabel="Change home server"
          confirmDisabled={setHome.isPending}
          onConfirm={() => setHome.mutate(pendingHome.machine_id)}
          onCancel={() => setPendingHome(null)}
        />
      )}
    </div>
  );
}
