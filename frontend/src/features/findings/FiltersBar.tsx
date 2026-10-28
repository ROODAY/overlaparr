import { useId, useState } from "react";
import type { Dashboard, FindingSort } from "../../api/types";
import { currentSort, type FilterKey } from "./params";

type Option = [value: string, label: string];

const STATIC: { key: FilterKey; label: string; options: Option[]; anyLabel?: string }[] = [
  {
    key: "type",
    label: "Type",
    options: [
      ["wanted_elsewhere", "Wanted elsewhere"],
      ["on_disk", "On disk"],
    ],
  },
  {
    key: "coverage",
    label: "Coverage",
    options: [
      ["full", "Full"],
      ["partial", "Partial"],
    ],
  },
  {
    key: "quality",
    label: "Quality",
    options: [
      ["better", "Better"],
      ["same", "Same"],
      ["worse", "Worse"],
      ["unknown", "Unknown"],
    ],
  },
  {
    key: "edition",
    label: "Edition",
    options: [
      ["same", "Same"],
      ["different", "Different"],
      ["unknown", "Unknown"],
    ],
  },
  {
    key: "match_method",
    label: "Match method",
    options: [
      ["id", "By ID"],
      ["number", "By number"],
    ],
  },
  {
    key: "review_state",
    label: "Review state",
    options: [
      ["new", "New"],
      ["reviewed", "Reviewed"],
      ["ignored", "Ignored"],
      ["actioned", "Actioned"],
    ],
  },
  {
    key: "lifecycle",
    label: "Lifecycle",
    anyLabel: "Active",
    options: [
      ["lost", "Lost"],
      ["removed", "Removed"],
      ["resolved", "Resolved"],
      ["all", "All"],
    ],
  },
];

const SORTS: Option[] = [
  ["title", "Title"],
  ["year", "Year"],
  ["size", "Size"],
  ["servers", "Servers"],
  ["updated", "Updated"],
];

const SELECT =
  "rounded border border-slate-600 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-accent focus:outline-none";

export interface FiltersBarProps {
  params: URLSearchParams;
  dashboard?: Dashboard;
  onChange: (updates: Record<string, string | null>) => void;
}

/** Filter selects, title search and sort; values are the GET /findings query params. */
export function FiltersBar({ params, dashboard, onChange }: FiltersBarProps) {
  const filters = [
    ...STATIC,
    {
      key: "instance" as const,
      label: "Instance",
      options: (dashboard?.instances ?? []).map((i): Option => [i.id, i.name]),
    },
    {
      key: "server" as const,
      label: "Server",
      options: (dashboard?.servers ?? []).map((s): Option => [s.id, s.label]),
    },
  ];
  const { sort, order } = currentSort(params);
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Search
        key={params.get("q") ?? ""}
        value={params.get("q") ?? ""} onChange={(q) => onChange({ q: q || null })} />
      {filters.map((f) => (
        <Select
          key={f.key}
          label={f.label}
          value={params.get(f.key) ?? ""}
          anyLabel={f.anyLabel ?? "Any"}
          options={f.options}
          onChange={(v) => onChange({ [f.key]: v || null })}
        />
      ))}
      <Select
        label="Sort by"
        value={sort}
        options={SORTS}
        onChange={(v) => onChange({ sort: v === "title" ? null : (v as FindingSort) })}
      />
      <button
        type="button"
        onClick={() => onChange({ order: order === "asc" ? "desc" : null })}
        className="rounded border border-slate-600 px-2 py-1 text-sm text-slate-200 hover:bg-slate-700"
      >
        {order === "asc" ? "Ascending" : "Descending"}
      </button>
    </div>
  );
}

function Search({ value, onChange }: { value: string; onChange: (q: string) => void }) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  return (
    <form
      className="flex flex-col gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        onChange(draft.trim());
      }}
    >
      <label htmlFor={id} className="text-xs text-slate-400">
        Search
      </label>
      <input
        id={id}
        type="search"
        value={draft}
        placeholder="Title…"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft.trim() !== value && onChange(draft.trim())}
        className={SELECT}
      />
    </form>
  );
}

function Select({
  label,
  value,
  options,
  anyLabel,
  onChange,
}: {
  label: string;
  value: string;
  options: Option[];
  anyLabel?: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  // A URL value outside the known options (e.g. a CSV list) is still shown, not dropped.
  const known = value === "" || options.some(([v]) => v === value);
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-slate-400">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={SELECT}>
        {anyLabel !== undefined && <option value="">{anyLabel}</option>}
        {!known && <option value={value}>{value}</option>}
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}
