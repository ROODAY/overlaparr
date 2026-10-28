import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { queryKeys } from "../../api/queryClient";
import type { ActionRequest, ActivityEntry } from "../../api/types";
import { useToast } from "../../components/useToast";

export interface Run {
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

/**
 * POST /actions with a result toast. On success the findings (list and detail) and the
 * dashboard are refetched and onDone runs; on error the toast shows the API detail.
 */
export function useRunActions(onDone?: () => void) {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ requests, confirm }: Run) =>
      api.post<ActivityEntry[]>("/actions", { requests, confirm }),
    onSuccess: (rows, run) => {
      const failed = rows.some((r) => r.result === "error");
      toast(resultMessage(run.label, rows), failed ? "error" : "info");
      void queryClient.invalidateQueries({ queryKey: queryKeys.findings });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
      onDone?.();
    },
    onError: (error, run) => {
      const detail = error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
      toast(`${run.label} failed: ${detail}`, "error");
    },
  });
}
