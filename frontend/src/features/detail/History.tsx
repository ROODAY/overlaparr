import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { queryKeys } from "../../api/queryClient";
import type { ActivityEntry } from "../../api/types";
import { useToast } from "../../components/useToast";
import {
  actionLabel,
  FILES_GONE,
  isDeleteRow,
  scopeText,
  undoLabel,
} from "../activity/labels";
import { formatDateTime } from "../../lib/format";

/** The finding's action log, newest first, with per-row undo. */
export function History({ rows }: { rows: ActivityEntry[] }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const undo = useMutation({
    mutationFn: (row: ActivityEntry) => api.post<ActivityEntry>(`/activity/${row.id}/undo`),
    onSuccess: (result, row) => {
      toast(
        result.result === "ok"
          ? `Undone: ${actionLabel(row)}`
          : `Undo failed: ${result.message ?? "unknown error"}`,
        result.result === "ok" ? "info" : "error",
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.findings });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
      toast(`Undo failed: ${detail}`, "error");
    },
  });

  if (rows.length === 0) return <p className="text-sm text-slate-400">No actions yet.</p>;
  return (
    <ul aria-label="History" className="divide-y divide-slate-800 rounded-lg border border-slate-700">
      {rows.map((row) => {
        const scope = scopeText(row.scope);
        // Deleted files never come back; undo of a delete only restores monitoring.
        const isDelete = isDeleteRow(row);
        return (
          <li key={row.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
            <span className="text-slate-400">{formatDateTime(row.ts)}</span>
            <span className="font-medium text-slate-100">{actionLabel(row)}</span>
            {scope && <span className="text-slate-400">{scope}</span>}
            <span className="text-slate-400">
              {row.actor === "auto" ? `Automation${row.rule_name ? `: ${row.rule_name}` : ""}` : "You"}
            </span>
            {row.result === "error" ? (
              <span className="text-red-300">Failed{row.message ? `: ${row.message}` : ""}</span>
            ) : (
              row.message && <span className="text-slate-300">{row.message}</span>
            )}
            {isDelete && <span className="font-medium text-red-300">{FILES_GONE}</span>}
            {row.result === "dry_run" && <span className="text-amber-300">Dry run</span>}
            <span className="ml-auto">
              {row.undone_at ? (
                <span className="text-slate-400">Undone {formatDateTime(row.undone_at)}</span>
              ) : row.can_undo ? (
                <button
                  type="button"
                  disabled={undo.isPending}
                  onClick={() => undo.mutate(row)}
                  className="rounded border border-slate-600 px-2 py-0.5 text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                >
                  {undoLabel(row)}
                </button>
              ) : (
                <span className="text-slate-500">Cannot be undone</span>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
