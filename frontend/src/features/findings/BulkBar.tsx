import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../../api/client";
import { queryKeys } from "../../api/queryClient";
import type { ActionName, ActionRequest, ActivityEntry } from "../../api/types";
import { useToast } from "../../components/useToast";
import { DeleteDialog } from "./DeleteDialog";

const SIMPLE: [ActionName, string][] = [
  ["unmonitor", "Unmonitor"],
  ["ignore", "Ignore"],
  ["mark_reviewed", "Mark reviewed"],
  ["exclude", "Exclude"],
  ["tag", "Tag"],
];

interface Run {
  label: string;
  requests: ActionRequest[];
  confirm: boolean;
}

/** "Unmonitor: 2 succeeded, 1 failed (first error)" from the action-log rows returned. */
function resultMessage(label: string, rows: ActivityEntry[]): string {
  const ok = rows.filter((r) => r.result === "ok").length;
  const failed = rows.filter((r) => r.result === "error");
  if (rows.length === 0) return `${label}: nothing to do`;
  const parts = [];
  if (ok) parts.push(`${ok} succeeded`);
  if (failed.length) parts.push(`${failed.length} failed`);
  const first = failed.find((r) => r.message)?.message;
  return `${label}: ${parts.join(", ")}${first ? ` (${first})` : ""}`;
}

export function BulkBar({ ids, onDone }: { ids: number[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [deleting, setDeleting] = useState(false);

  const mutation = useMutation({
    mutationFn: ({ requests, confirm }: Run) =>
      api.post<ActivityEntry[]>("/actions", { requests, confirm }),
    onSuccess: (rows, run) => {
      const failed = rows.some((r) => r.result === "error");
      toast(resultMessage(run.label, rows), failed ? "error" : "info");
      void queryClient.invalidateQueries({ queryKey: queryKeys.findings });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
      onDone();
    },
    onError: (error, run) => {
      const detail = error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
      toast(`${run.label} failed: ${detail}`, "error");
    },
  });

  const run = (label: string, action: ActionName) =>
    mutation.mutate({
      label,
      confirm: false,
      requests: ids.map((id) => ({ finding_id: id, action, scope: "item" })),
    });

  return (
    <section
      aria-label="Bulk actions"
      className="sticky top-0 z-20 flex flex-wrap items-center gap-2 rounded-lg border border-accent/50 bg-slate-800 px-3 py-2 text-sm"
    >
      <span className="mr-2 font-medium text-slate-100">{ids.length} selected</span>
      {SIMPLE.map(([action, label]) => (
        <button
          key={action}
          type="button"
          disabled={mutation.isPending}
          onClick={() => run(label, action)}
          className="rounded px-3 py-1 text-slate-200 hover:bg-slate-700 disabled:opacity-50"
        >
          {label}
        </button>
      ))}
      <button
        type="button"
        disabled={mutation.isPending}
        onClick={() => setDeleting(true)}
        className="rounded px-3 py-1 text-red-300 hover:bg-red-950 disabled:opacity-50"
      >
        Delete…
      </button>
      <button
        type="button"
        onClick={onDone}
        className="ml-auto rounded px-3 py-1 text-slate-400 hover:bg-slate-700"
      >
        Clear selection
      </button>
      {deleting && (
        <DeleteDialog
          ids={ids}
          onCancel={() => setDeleting(false)}
          onConfirm={(requests) => {
            setDeleting(false);
            mutation.mutate({ label: "Delete", requests, confirm: true });
          }}
        />
      )}
    </section>
  );
}
