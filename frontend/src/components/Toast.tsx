import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ToastContext, type ShowToast, type ToastVariant } from "./useToast";

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
}

const DISMISS_MS = 5000;

const VARIANTS: Record<ToastVariant, string> = {
  info: "border-slate-700 bg-slate-800 text-slate-100",
  error: "border-red-500/40 bg-red-950 text-red-100",
};

/** Holds the toast stack; each toast dismisses itself after a few seconds. */
export function ToastProvider({
  children,
  dismissMs = DISMISS_MS,
}: {
  children: ReactNode;
  dismissMs?: number;
}) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((all) => all.filter((t) => t.id !== id));
  }, []);

  const show = useCallback<ShowToast>(
    (message, variant = "info") => {
      const id = nextId.current++;
      setToasts((all) => [...all, { id, message, variant }]);
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        dismiss(id);
      }, dismissMs);
      timers.current.add(timer);
    },
    [dismiss, dismissMs],
  );

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed right-4 bottom-4 left-4 z-50 flex flex-col items-end gap-2 sm:left-auto"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex max-w-sm items-start gap-3 rounded border px-4 py-2 text-sm shadow-lg ${VARIANTS[t.variant]}`}
          >
            <span>{t.message}</span>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
              className="ml-auto text-slate-400 hover:text-slate-100"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
