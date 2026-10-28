import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import type { DeleteMode, RuleAction, RuleWarning } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";

/**
 * The warning shown before a rule is enabled: GET /rules/warning?action=.
 * Delete rules also require typing DELETE (that confirmation sets delete_confirmed).
 */
export function EnableDialog({
  open,
  action,
  deleteMode,
  ruleName,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  action: RuleAction;
  /** Chosen delete mode; "remove_item" deletes the whole item and its files. */
  deleteMode?: DeleteMode;
  ruleName: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const warning = useQuery({
    queryKey: ["rules", "warning", action],
    queryFn: () => api.get<RuleWarning>(`/rules/warning?action=${encodeURIComponent(action)}`),
    enabled: open,
    staleTime: Infinity,
  });
  const isDelete = action === "delete";
  const removesItem = isDelete && deleteMode === "remove_item";

  return (
    <ConfirmDialog
      open={open}
      title={`Enable “${ruleName}”?`}
      destructive={isDelete}
      requireText={isDelete ? "DELETE" : undefined}
      confirmDisabled={!warning.isSuccess}
      confirmLabel={confirmLabel}
      onConfirm={onConfirm}
      onCancel={onCancel}
      body={
        warning.isPending ? (
          <p>Loading…</p>
        ) : warning.isError ? (
          <p role="alert" className="text-red-300">
            {warning.error instanceof ApiError ? warning.error.detail : "Could not load the warning"}
          </p>
        ) : (
          <div>
            <p className={isDelete ? "text-red-200" : undefined}>{warning.data.text}</p>
            {removesItem && (
              <p className="mt-2 text-red-200">
                This removes whole movies/series from Radarr/Sonarr with their files. Loss
                handling cannot restore them.
              </p>
            )}
          </div>
        )
      }
    />
  );
}
