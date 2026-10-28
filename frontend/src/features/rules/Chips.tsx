import type { ReactNode } from "react";

export type ChipOption<T extends string> = [value: T, label: string];

/** One labelled row of the rule editor (mockup: label column + controls). */
export function FieldRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-slate-700/60 py-3 sm:flex-row sm:items-start">
      <div className="w-48 shrink-0 pt-1 text-sm text-slate-300">{label}</div>
      <div className="min-w-0 flex-1 space-y-1">
        {children}
        {hint && <p className="text-xs text-slate-400">{hint}</p>}
      </div>
    </div>
  );
}

export function Chip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs ${
        pressed
          ? "border-accent bg-accent/20 text-blue-200"
          : "border-slate-600 bg-slate-800 text-slate-300 hover:border-slate-500"
      }`}
    >
      {children}
    </button>
  );
}

export function ChipGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2">
      {children}
    </div>
  );
}

/** Pick exactly one value. */
export function SingleChips<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ChipOption<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <ChipGroup label={label}>
      {options.map(([v, text]) => (
        <Chip key={v} pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </Chip>
      ))}
    </ChipGroup>
  );
}

/** Toggle any number of values; the result keeps the options' order. */
export function MultiChips<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ChipOption<T>[];
  value: T[];
  onChange: (value: T[]) => void;
}) {
  const toggle = (v: T) => {
    const next = value.includes(v) ? value.filter((x) => x !== v) : [...value, v];
    onChange(options.map(([o]) => o).filter((o) => next.includes(o)));
  };
  return (
    <ChipGroup label={label}>
      {options.map(([v, text]) => (
        <Chip key={v} pressed={value.includes(v)} onClick={() => toggle(v)}>
          {text}
        </Chip>
      ))}
    </ChipGroup>
  );
}

/**
 * "All"/"Any" plus specific ids: an empty list means all. Picking a specific id
 * leaves "all"; clearing the last one goes back to it.
 */
export function AnyOrSomeChips({
  label,
  allLabel,
  options,
  value,
  onChange,
}: {
  label: string;
  allLabel: string;
  options: ChipOption<string>[];
  value: string[];
  onChange: (value: string[]) => void;
}) {
  // Ids saved in the rule but no longer configured still show, so they can be removed.
  const known = new Set(options.map(([v]) => v));
  const all = [...options, ...value.filter((v) => !known.has(v)).map((v): ChipOption<string> => [v, v])];
  const toggle = (v: string) =>
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  return (
    <ChipGroup label={label}>
      <Chip pressed={value.length === 0} onClick={() => onChange([])}>
        {allLabel}
      </Chip>
      {all.map(([v, text]) => (
        <Chip key={v} pressed={value.includes(v)} onClick={() => toggle(v)}>
          {text}
        </Chip>
      ))}
    </ChipGroup>
  );
}
