import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { queryKeys } from "../../api/queryClient";
import { useToast } from "../../components/useToast";
import { DRY_RUN_COPY } from "./labels";

/** POST /rules/{id}/dry-run: the next scan reports without changing anything. */
export function DryRunButton({
  rule,
  onQueued,
  className = "",
}: {
  rule: { id: string; dry_run_next: boolean };
  onQueued?: () => void;
  className?: string;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const mutation = useMutation({
    mutationFn: () => api.post(`/rules/${rule.id}/dry-run`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.rules });
      toast(DRY_RUN_COPY);
      onQueued?.();
    },
    onError: (e) =>
      toast(
        `Dry run failed: ${e instanceof ApiError ? e.detail : "could not reach Overlaparr"}`,
        "error",
      ),
  });

  return (
    <button
      type="button"
      disabled={rule.dry_run_next || mutation.isPending}
      title={DRY_RUN_COPY}
      onClick={() => mutation.mutate()}
      className={`rounded px-3 py-1 text-slate-300 hover:bg-slate-700 disabled:cursor-default disabled:text-amber-300 ${className}`}
    >
      {rule.dry_run_next ? "Dry run queued" : "Dry run"}
    </button>
  );
}
