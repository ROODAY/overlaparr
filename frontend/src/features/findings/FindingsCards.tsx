import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Finding } from "../../api/types";
import { Badge } from "../../components/Badge";
import { DiffBadges } from "../../components/DiffBadges";
import { formatBytes } from "../../lib/format";
import { ElsewhereChips, Poster, TypeLabel, type SelectionProps } from "./FindingParts";

export interface FindingsCardsProps extends SelectionProps {
  items: Finding[];
  total: number;
}

export function FindingsCards({ items, total, selected, onToggle, onToggleAll }: FindingsCardsProps) {
  const ids = items.map((f) => f.id);
  const allSelected = ids.length > 0 && ids.every((id) => selected.has(id));
  return (
    <div className="space-y-4">
      <Summary items={items} total={total}>
        <label className="ml-auto flex items-center gap-2">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={(e) => onToggleAll(ids, e.target.checked)}
          />
          Select all on this page
        </label>
      </Summary>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
        {items.map((f) => (
          <Card key={f.id} finding={f} checked={selected.has(f.id)} onToggle={onToggle} />
        ))}
      </div>
    </div>
  );
}

function Summary({
  items,
  total,
  children,
}: {
  items: Finding[];
  total: number;
  children: ReactNode;
}) {
  const full = items.filter((f) => f.badges.coverage === "full").length;
  const bytes = items.reduce((sum, f) => sum + (f.yours.size ?? 0), 0);
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm text-slate-300">
      <span>
        <strong className="text-slate-100">{total}</strong> findings
      </span>
      <span>
        This page: {full} full · {items.length - full} partial · {formatBytes(bytes)} yours
      </span>
      {children}
    </div>
  );
}

function Card({
  finding: f,
  checked,
  onToggle,
}: {
  finding: Finding;
  checked: boolean;
  onToggle: (id: number) => void;
}) {
  const titleId = useId();
  return (
    <article
      aria-labelledby={titleId}
      className={`relative flex flex-col overflow-hidden rounded-lg border bg-slate-800 ${
        checked ? "border-accent" : "border-slate-700"
      }`}
    >
      <input
        type="checkbox"
        aria-label={`Select ${f.title}`}
        checked={checked}
        onChange={() => onToggle(f.id)}
        className="absolute top-2 left-2 z-10"
      />
      <Link to={`/findings/${f.id}`} className="block">
        <Poster finding={f} className="aspect-[2/3] w-full text-sm" />
      </Link>
      <div className="flex flex-1 flex-col gap-1 p-2 text-xs">
        <Link
          id={titleId}
          to={`/findings/${f.id}`}
          className="text-sm font-medium text-slate-100 hover:underline"
        >
          {f.title}
        </Link>
        {f.automation_hold && (
          <Badge variant="caution" title="Automation is paused for this item">
            Hold
          </Badge>
        )}
        <span className="text-slate-400">
          {f.year ?? "—"} · {f.instance.name} · <TypeLabel finding={f} />
        </span>
        <DiffBadges badges={f.badges} kind={f.kind} />
        <ElsewhereChips finding={f} />
      </div>
    </article>
  );
}
