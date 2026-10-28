import { useState } from "react";
import type { Finding } from "../../api/types";
import { DeleteDialog } from "../findings/DeleteDialog";
import { useRunActions } from "../findings/useRunActions";
import { BUTTON, DANGER_BUTTON } from "./styles";

/**
 * Whole-item actions (always manual): unmonitor, ignore (whole finding or one server's
 * copies) and delete with the delete-mode choice.
 */
export function ItemActions({ finding }: { finding: Finding }) {
  const series = finding.kind === "series";
  const [deleting, setDeleting] = useState(false);
  const mutation = useRunActions();
  const item = { finding_id: finding.id, scope: "item" as const };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        className={BUTTON}
        disabled={mutation.isPending}
        onClick={() =>
          mutation.mutate({
            label: series ? "Unmonitor series" : "Unmonitor",
            confirm: false,
            requests: [{ ...item, action: "unmonitor" }],
          })
        }
      >
        {series ? "Unmonitor series" : "Unmonitor"}
      </button>
      <IgnoreMenu
        finding={finding}
        disabled={mutation.isPending}
        onIgnore={(serverId, label) =>
          mutation.mutate({
            label,
            confirm: false,
            requests: [
              serverId
                ? { ...item, action: "ignore_server", server_id: serverId }
                : { ...item, action: "ignore" },
            ],
          })
        }
      />
      <button
        type="button"
        className={DANGER_BUTTON}
        disabled={mutation.isPending}
        onClick={() => setDeleting(true)}
      >
        {series ? "Delete series…" : "Delete…"}
      </button>
      {deleting && (
        <DeleteDialog
          title={series ? "Delete series" : "Delete movie"}
          targets={[{ finding_id: finding.id, title: finding.title }]}
          onCancel={() => setDeleting(false)}
          onConfirm={(requests) => {
            setDeleting(false);
            mutation.mutate({ label: "Delete", requests, confirm: true });
          }}
        />
      )}
    </div>
  );
}

function IgnoreMenu({
  finding,
  disabled,
  onIgnore,
}: {
  finding: Finding;
  disabled: boolean;
  onIgnore: (serverId: string | null, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const servers = finding.elsewhere.filter((e) => !e.ignored);
  const wholeIgnored = finding.review_state === "ignored";
  const choose = (serverId: string | null, label: string) => {
    setOpen(false);
    onIgnore(serverId, label);
  };
  const item = "block w-full px-3 py-1.5 text-left hover:bg-slate-700";
  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        className={BUTTON}
        disabled={disabled || (wholeIgnored && servers.length === 0)}
        onClick={() => setOpen(!open)}
      >
        Ignore…
      </button>
      {open && (
        <div
          role="group"
          aria-label="Ignore"
          className="absolute right-0 z-30 mt-1 w-56 rounded border border-slate-600 bg-slate-800 py-1 text-sm text-slate-200 shadow-lg"
        >
          {!wholeIgnored && (
            <button type="button" className={item} onClick={() => choose(null, "Ignore")}>
              Whole finding
            </button>
          )}
          {servers.map((s) => (
            <button
              key={s.server_id}
              type="button"
              className={item}
              onClick={() => choose(s.server_id, `Ignore ${s.label}`)}
            >
              Only {s.label}'s copies
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
