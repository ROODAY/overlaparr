import type { ReactNode } from "react";

export type BadgeVariant = "good" | "caution" | "bad" | "neutral";

const VARIANTS: Record<BadgeVariant, string> = {
  good: "bg-green-500/15 text-green-300 ring-green-500/30",
  caution: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  bad: "bg-red-500/15 text-red-300 ring-red-500/30",
  neutral: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
};

export function Badge({
  variant = "neutral",
  title,
  children,
}: {
  variant?: BadgeVariant;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${VARIANTS[variant]}`}
    >
      {children}
    </span>
  );
}
