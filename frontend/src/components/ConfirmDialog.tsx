import { useEffect, useId, useState, type ReactNode } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  /** Red confirm button for actions that cannot be undone. */
  destructive?: boolean;
  /** When set, confirm stays disabled until the user types exactly this text. */
  requireText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog(props: ConfirmDialogProps) {
  if (!props.open) return null;
  // Mounted only while open, so the typed text resets each time it opens.
  return <DialogContent {...props} />;
}

function DialogContent({
  title,
  body,
  confirmLabel,
  destructive = false,
  requireText,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  const titleId = useId();
  const inputId = useId();
  const blocked = requireText !== undefined && typed !== requireText;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-lg border border-slate-700 bg-slate-800 p-5 shadow-xl"
      >
        <h2 id={titleId} className="text-lg font-semibold text-slate-100">
          {title}
        </h2>
        <div className="mt-2 text-sm text-slate-300">{body}</div>
        <form
          className="mt-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!blocked) onConfirm();
          }}
        >
          {requireText !== undefined && (
            <div className="mb-4">
              <label htmlFor={inputId} className="block text-sm text-slate-300">
                Type <span className="font-mono font-semibold text-slate-100">{requireText}</span> to
                confirm
              </label>
              <input
                id={inputId}
                autoFocus
                autoComplete="off"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                className="mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100 focus:border-accent focus:outline-none"
              />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              autoFocus={requireText === undefined}
              onClick={onCancel}
              className="rounded px-4 py-2 text-sm text-slate-300 hover:bg-slate-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={blocked}
              className={`rounded px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 ${
                destructive ? "bg-red-600 hover:bg-red-500" : "bg-accent hover:bg-accent-hover"
              }`}
            >
              {confirmLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
