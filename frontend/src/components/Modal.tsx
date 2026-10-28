import { useEffect, useId, type ReactNode } from "react";

const SIZES = { md: "max-w-lg", lg: "max-w-2xl" } as const;

/** Overlay dialog with a titled header and a close button; Escape closes it. */
export function Modal({
  title,
  size = "md",
  onClose,
  children,
}: {
  title: string;
  size?: keyof typeof SIZES;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/60 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`mt-16 w-full ${SIZES[size]} rounded-lg border border-slate-700 bg-slate-800 p-5 shadow-xl`}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold text-slate-100">
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded px-2 text-xl text-slate-400 hover:bg-slate-700 hover:text-slate-100"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
