import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Finding, FindingSort, SortOrder } from "../../api/types";
import { Badge } from "../../components/Badge";
import { DiffBadges } from "../../components/DiffBadges";
import { formatBytes } from "../../lib/format";
import { ElsewhereChips, Poster, TypeLabel, Yours, type SelectionProps } from "./FindingParts";

export interface FindingsTableProps extends SelectionProps {
  items: Finding[];
  sort: FindingSort;
  order: SortOrder;
  onSort: (sort: FindingSort) => void;
}

export function FindingsTable({
  items,
  sort,
  order,
  onSort,
  selected,
  onToggle,
  onToggleAll,
}: FindingsTableProps) {
  const ids = items.map((f) => f.id);
  const allSelected = ids.length > 0 && ids.every((id) => selected.has(id));
  const sortHeader = (key: FindingSort, label: string) => (
    <SortHeader active={sort === key} order={order} onClick={() => onSort(key)}>
      {label}
    </SortHeader>
  );
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-700">
      <table className="w-full text-left text-sm text-slate-200">
        <thead className="bg-slate-800 text-xs text-slate-400 uppercase">
          <tr>
            <th className="w-8 px-3 py-2">
              <input
                type="checkbox"
                aria-label="Select all on this page"
                checked={allSelected}
                onChange={(e) => onToggleAll(ids, e.target.checked)}
              />
            </th>
            <th className="px-2 py-2">
              <span className="sr-only">Poster</span>
            </th>
            {sortHeader("title", "Title")}
            <th className="px-3 py-2">Type</th>
            <th className="px-3 py-2">Yours</th>
            {sortHeader("servers", "Elsewhere")}
            <th className="px-3 py-2">Differences</th>
            {sortHeader("size", "Size")}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {items.map((f) => (
            <tr key={f.id} className="hover:bg-slate-800/50">
              <td className="px-3 py-2">
                <input
                  type="checkbox"
                  aria-label={`Select ${f.title}`}
                  checked={selected.has(f.id)}
                  onChange={() => onToggle(f.id)}
                />
              </td>
              <td className="px-2 py-2">
                <Poster finding={f} className="h-12 w-8" />
              </td>
              <td className="px-3 py-2">
                <Link to={`/findings/${f.id}`} className="font-medium text-slate-100 hover:underline">
                  {f.title}
                  {f.year != null && <span className="text-slate-400"> ({f.year})</span>}
                </Link>
                {f.automation_hold && (
                  <>
                    {" "}
                    <Badge variant="caution" title="Automation is paused for this item">
                      Hold
                    </Badge>
                  </>
                )}
                <div className="text-xs text-slate-400">{f.instance.name}</div>
              </td>
              <td className="px-3 py-2 whitespace-nowrap"><TypeLabel finding={f} /></td>
              <td className="px-3 py-2 whitespace-nowrap">
                <Yours finding={f} />
              </td>
              <td className="px-3 py-2">
                <ElsewhereChips finding={f} />
              </td>
              <td className="px-3 py-2">
                <DiffBadges badges={f.badges} kind={f.kind} />
              </td>
              <td className="px-3 py-2 whitespace-nowrap">
                {f.yours.size != null ? formatBytes(f.yours.size) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SortHeader({
  active,
  order,
  onClick,
  children,
}: {
  active: boolean;
  order: SortOrder;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <th
      className="px-3 py-2"
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : undefined}
    >
      <button type="button" onClick={onClick} className="uppercase hover:text-slate-200">
        {children}
        {active && <span aria-hidden="true">{order === "asc" ? " ▲" : " ▼"}</span>}
      </button>
    </th>
  );
}
