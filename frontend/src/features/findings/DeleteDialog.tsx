import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { ActionPreview, ActionRequest, DeleteMode } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { formatBytes } from "../../lib/format";

function deleteRequests(
  ids: number[],
  mode: DeleteMode,
  addExclusion: boolean,
): ActionRequest[] {
  return ids.map((id) => ({
    finding_id: id,
    action: "delete",
    scope: "item",
    delete_mode: mode,
    add_exclusion: mode === "remove_item" && addExclusion,
  }));
}

export interface DeleteDialogProps {
  ids: number[];
  onConfirm: (requests: ActionRequest[]) => void;
  onCancel: () => void;
}

/** Bulk delete confirmation fed by POST /actions/preview (count + space freed). */
export function DeleteDialog({ ids, onConfirm, onCancel }: DeleteDialogProps) {
  const [mode, setMode] = useState<DeleteMode>("files");
  const [addExclusion, setAddExclusion] = useState(false);
  const requests = deleteRequests(ids, mode, addExclusion);
  const preview = useQuery({
    queryKey: ["actions-preview", requests],
    queryFn: () => api.post<ActionPreview>("/actions/preview", { requests }),
  });
  const name = useId();

  const body = (
    <div className="space-y-3">
      <PreviewText preview={preview.data} error={preview.error} selected={ids.length} />
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
      {mode === "remove_item" && (
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
      title="Delete selected findings"
      body={body}
      confirmLabel="Delete"
      destructive
      confirmDisabled={!preview.data || preview.data.items === 0}
      onConfirm={() => onConfirm(requests)}
      onCancel={onCancel}
    />
  );
}

function PreviewText({
  preview,
  error,
  selected,
}: {
  preview?: ActionPreview;
  error: Error | null;
  selected: number;
}) {
  if (error) {
    return (
      <p role="alert" className="text-red-300">
        {error instanceof ApiError ? error.detail : "Could not preview the delete"}
      </p>
    );
  }
  if (!preview) return <p className="text-slate-400">Checking what will be deleted…</p>;
  const items = `${preview.items} ${preview.items === 1 ? "item" : "items"}`;
  const episodes = preview.episodes > 0 ? ` (${preview.episodes} episodes)` : "";
  const skipped = selected - preview.items;
  return (
    <div className="space-y-1">
      <p>
        Deletes {items}
        {episodes}, freeing <strong className="text-slate-100">{formatBytes(preview.bytes)}</strong>.
      </p>
      {skipped > 0 && (
        <p className="text-amber-300">
          {skipped} of the selected findings cannot be deleted and will be skipped.
        </p>
      )}
    </div>
  );
}
