import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { Rule, RuleIn, RuleWithRun } from "../api/types";
import { Switch } from "../components/Switch";
import { useToast } from "../components/useToast";
import { DryRunButton } from "../features/rules/DryRunButton";
import { EnableDialog } from "../features/rules/EnableDialog";
import { ACTION_LABELS } from "../features/rules/labels";
import { formatRelative } from "../lib/format";

export function Automation() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [pendingEnable, setPendingEnable] = useState<RuleWithRun | null>(null);
  const rules = useQuery({
    queryKey: queryKeys.rules,
    queryFn: () => api.get<RuleWithRun[]>("/rules"),
  });

  const setEnabled = useMutation({
    mutationFn: ({ id, body }: { id: string; body: RuleIn }) => api.patch<Rule>(`/rules/${id}`, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.rules }),
    onError: (e) =>
      toast(
        `Could not update the rule: ${e instanceof ApiError ? e.detail : "could not reach Overlaparr"}`,
        "error",
      ),
  });

  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.post<Rule[]>("/rules/reorder", { ids }),
    // Reorder returns plain rules (no last_run/last_affected): keep the cached run
    // fields and only take the new order.
    onSuccess: (data) =>
      queryClient.setQueryData<RuleWithRun[]>(queryKeys.rules, (old) => {
        if (!old) return old;
        const byId = new Map(old.map((rule) => [rule.id, rule]));
        return data.map(
          (rule) => byId.get(rule.id) ?? { ...rule, last_run: null, last_affected: 0 },
        );
      }),
    onError: (e) =>
      toast(
        `Could not reorder: ${e instanceof ApiError ? e.detail : "could not reach Overlaparr"}`,
        "error",
      ),
  });

  const toggle = (rule: RuleWithRun, enabled: boolean) => {
    if (enabled) setPendingEnable(rule); // show the warning first
    else setEnabled.mutate({ id: rule.id, body: { enabled: false } });
  };

  const move = (index: number, delta: number) => {
    if (!rules.data) return;
    const ids = rules.data.map((r) => r.id);
    const [moved] = ids.splice(index, 1);
    ids.splice(index + delta, 0, moved);
    reorder.mutate(ids);
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">Automation</h1>
          <p className="mt-1 text-sm text-slate-400">
            Rules run after each scan in list order. Rules start disabled.
          </p>
        </div>
        <Link
          to="/automation/new"
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
        >
          New rule
        </Link>
      </div>

      {rules.isPending ? (
        <p className="mt-6 text-slate-400">Loading…</p>
      ) : rules.isError ? (
        <p role="alert" className="mt-6 text-red-300">
          {rules.error instanceof ApiError ? rules.error.detail : "Could not load rules"}
        </p>
      ) : rules.data.length === 0 ? (
        <p className="mt-6 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
          No rules yet. Create one to act on findings automatically after each scan.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2">Rule</th>
                <th className="px-3 py-2">Enabled</th>
                <th className="px-3 py-2">Last run</th>
                <th className="px-3 py-2 text-right">Items affected</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/60">
              {rules.data.map((rule, index) => (
                <tr key={rule.id} className="align-middle">
                  <td className="px-3 py-2">
                    <Link to={`/automation/${rule.id}`} className="text-slate-100 hover:underline">
                      {rule.name}
                    </Link>
                    <div className="text-xs text-slate-400">
                      {ACTION_LABELS[rule.action]}
                      {rule.action === "delete" && rule.delete_mode === "remove_item"
                        ? " · remove from Radarr"
                        : ""}
                      {rule.dry_run_next ? " · dry run queued" : ""}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <Switch
                      label={`Enable ${rule.name}`}
                      checked={rule.enabled}
                      disabled={setEnabled.isPending}
                      onChange={(enabled) => toggle(rule, enabled)}
                    />
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {rule.last_run ? formatRelative(rule.last_run) : "Never"}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-300">{rule.last_affected}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <DryRunButton rule={rule} />
                      <button
                        type="button"
                        aria-label="Move up"
                        disabled={index === 0 || reorder.isPending}
                        onClick={() => move(index, -1)}
                        className="rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:opacity-30"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        aria-label="Move down"
                        disabled={index === rules.data.length - 1 || reorder.isPending}
                        onClick={() => move(index, 1)}
                        className="rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:opacity-30"
                      >
                        ↓
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pendingEnable && (
        <EnableDialog
          open
          action={pendingEnable.action}
          ruleName={pendingEnable.name}
          confirmLabel="Enable"
          onCancel={() => setPendingEnable(null)}
          onConfirm={() => {
            const body: RuleIn =
              pendingEnable.action === "delete"
                ? { enabled: true, delete_confirmed: true }
                : { enabled: true };
            setEnabled.mutate({ id: pendingEnable.id, body });
            setPendingEnable(null);
          }}
        />
      )}
    </div>
  );
}
