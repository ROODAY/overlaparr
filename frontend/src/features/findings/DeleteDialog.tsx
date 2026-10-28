import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ActionPreview, ActionRequest, ActionScope, DeleteMode } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { formatBytes } from "../../lib/format";

/** One thing to delete: a whole finding, or selected episodes of a series. */
export interface DeleteTarget {
  finding_id: number;
  title: string;
  scope?: ActionScope;
  episode_ids?: number[];
}

function deleteRequests(
  targets: DeleteTarget[],
  mode: DeleteMode,
  addExclusion: boolean,
): ActionRequest[] {
  return targets.map((t) => ({
    finding_id: t.finding_id,
    action: "delete",
    scope: t.scope ?? "item",
    ...(t.scope === "episodes" ? { episode_ids: t.episode_ids ?? [] } : {}),
    delete_mode: mode,
    add_exclusion: mode === "remove_item" && addExclusion,
  }));
}

export interface DeleteDialogProps {
  targets: DeleteTarget[];
  title?: string;
  /** Called with only the requests the preview accepted. */
  onConfirm: (requests: ActionRequest[]) => void;
  onCancel: () => void;
}

/**
 * Delete confirmation fed by POST /actions/preview: what will be deleted, what cannot be,
 * and the space freed. "Remove from Sonarr/Radarr" is offered for whole items only.
 */
export function DeleteDialog({
  targets,
  title = "Delete selected findings",
  onConfirm,
  onCancel,
}: DeleteDialogProps) {
  const wholeItems = targets.every((t) => (t.scope ?? "item") === "item");
  const [mode, setMode] = useState<DeleteMode>("files");
  const [addExclusion, setAddExclusion] = useState(false);
  const requests = deleteRequests(targets, wholeItems ? mode : "files", addExclusion);
  const preview = useQuery({
    queryKey: ["actions-preview", requests],
    queryFn: () => api.post<ActionPreview>("/actions/preview", { requests }),
    // A preview is only valid for the moment it was asked: never show, or act on, a
    // cached one when the dialog reopens or the delete mode changes.
    staleTime: 0,
    gcTime: 0,
  });
  const rejected = new Set((preview.data?.rejected ?? []).map((r) => r.index));
  const accepted = requests.filter((_, i) => !rejected.has(i));
  // Selected episodes without files: there is nothing to delete.
  const nothing = !wholeItems && preview.data?.episodes === 0;
  const name = useId();

  const body = (
    <div className="space-y-3">
      {nothing ? (
        <p className="text-amber-300">Nothing to delete: none of the selected episodes have files.</p>
      ) : (
        <PreviewText preview={preview.data} error={preview.error} targets={targets} />
      )}
      {wholeItems && (
        <fieldset className="space-y-1">
          <legend className="sr-only">Delete mode</legend>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name={name}
              checked={mode === "files"}
              onChange={() => setMode("files")}
            />
            Delete files and unmonitor
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name={name}
              checked={mode === "remove_item"}
              onChange={() => setMode("remove_item")}
            />
            Remove from Sonarr/Radarr
          </label>
        </fieldset>
      )}
      {wholeItems && mode === "remove_item" && (
        <div className="space-y-1 rounded border border-red-500/40 bg-red-950/40 p-2">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={addExclusion}
              onChange={(e) => setAddExclusion(e.target.checked)}
            />
            Also add exclusion
          </label>
          <p className="text-red-200">
            The whole movie or series is removed with its files and the finding becomes removed:
            it cannot be restored automatically.
          </p>
        </div>
      )}
      <p className="font-medium text-red-300">Deleted files cannot be undone.</p>
    </div>
  );

  return (
    <ConfirmDialog
      open
      title={title}
      body={body}
      confirmLabel="Delete"
      destructive
      confirmDisabled={
        preview.isFetching ||
        !preview.data ||
        preview.data.items === 0 ||
        accepted.length === 0 ||
        nothing
      }
      onConfirm={() => onConfirm(accepted)}
      onCancel={onCancel}
    />
  );
}

function PreviewText({
  preview,
  error,
  targets,
}: {
  preview?: ActionPreview;
  error: Error | null;
  targets: DeleteTarget[];
}) {
  if (error) {
    return (
      <p role="alert" className="text-red-300">
        {error instanceof ApiError ? error.detail : "Could not preview the delete"}
      </p>
    );
  }
  if (!preview) return <p className="text-slate-400">Checking what will be deleted…</p>;
  const titleOf = (id: number) => targets.find((t) => t.finding_id === id)?.title ?? `#${id}`;
  const items = `${preview.items} ${preview.items === 1 ? "item" : "items"}`;
  const episodes = preview.episodes > 0 ? ` (${preview.episodes} episodes)` : "";
  return (
    <div className="space-y-2">
      <p>
        Deletes {items}
        {episodes}, freeing <strong className="text-slate-100">{formatBytes(preview.bytes)}</strong>.
      </p>
      {preview.per_finding.length > 0 && (
        <ul aria-label="Will be deleted" className="max-h-40 list-disc overflow-y-auto pl-5">
          {preview.per_finding.map((p) => (
            <li key={p.finding_id}>
              {titleOf(p.finding_id)}
              {p.episodes > 0 && (
                <span className="text-slate-400">
                  {" "}
                  ({p.episodes} {p.episodes === 1 ? "episode" : "episodes"})
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {preview.rejected.length > 0 && (
        <ul aria-label="Cannot be deleted" className="space-y-0.5 text-amber-300">
          {preview.rejected.map((r) => (
            <li key={r.index}>
              {titleOf(r.finding_id)} cannot be deleted: {r.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
