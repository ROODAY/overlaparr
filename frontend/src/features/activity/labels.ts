// Display helpers for action-log rows, shared by the Activity page and a finding's History.

import type { ActivityEntry } from "../../api/types";

export const ACTION_LABELS: Record<string, string> = {
  unmonitor: "Unmonitor",
  delete: "Delete",
  exclude: "Add exclusion",
  tag: "Tag",
  ignore: "Ignore",
  ignore_server: "Ignore server",
  mark_reviewed: "Mark reviewed",
  unignore: "Un-ignore",
  mark_new: "Mark new",
  quality_profile: "Set quality profile",
  search: "Search",
  clear_hold: "Clear automation hold",
};

/** Deleted files never come back; only the unmonitor part of a delete can be undone. */
export const FILES_GONE = "Files cannot be restored";

/** The action's display name, prefixed "Undo:" for a row that reverses an earlier action. */
export function actionLabel(row: ActivityEntry): string {
  const label = ACTION_LABELS[row.action] ?? row.action.replaceAll("_", " ");
  return row.undo_of_id != null ? `Undo: ${label}` : label;
}

/** What the action covered: episode count, removal from the app, server. */
export function scopeText(scope: Record<string, unknown>): string | null {
  const parts = [];
  if (Array.isArray(scope.episode_ids)) {
    const n = scope.episode_ids.length;
    parts.push(`${n} ${n === 1 ? "episode" : "episodes"}`);
  }
  if (scope.delete_mode === "remove_item") parts.push("removed from the app");
  if (typeof scope.server_id === "string") parts.push(`server ${scope.server_id}`);
  return parts.length ? parts.join(", ") : null;
}

/** A delete whose files are gone for good (a reversal of a delete is not a delete). */
export function isDeleteRow(row: ActivityEntry): boolean {
  return row.action === "delete" && row.undo_of_id == null;
}

/** The undo button's label: a delete can only restore monitoring. */
export function undoLabel(row: ActivityEntry): string {
  return isDeleteRow(row) ? "Undo unmonitor" : "Undo";
}
