import { useState } from "react";
import type { ActionName, Finding } from "../../api/types";
import { DeleteDialog } from "./DeleteDialog";
import { useRunActions } from "./useRunActions";

const SIMPLE: [ActionName, string][] = [
  ["unmonitor", "Unmonitor"],
  ["ignore", "Ignore"],
  ["mark_reviewed", "Mark reviewed"],
  ["exclude", "Exclude"],
  ["tag", "Tag"],
];

export function BulkBar({ items, onDone }: { items: Finding[]; onDone: () => void }) {
  const ids = items.map((f) => f.id);
  const [deleting, setDeleting] = useState(false);
  const mutation = useRunActions(onDone);

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
          targets={items.map((f) => ({ finding_id: f.id, title: f.title }))}
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
