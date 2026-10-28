import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { Dashboard, Finding, FindingSort, Page } from "../api/types";
import { BulkBar } from "../features/findings/BulkBar";
import { FiltersBar } from "../features/findings/FiltersBar";
import { FindingsCards } from "../features/findings/FindingsCards";
import { FindingsTable } from "../features/findings/FindingsTable";
import {
  apiQuery,
  currentPage,
  currentSort,
  PAGE_SIZE,
  withParams,
} from "../features/findings/params";

type View = "table" | "posters";
const VIEW_KEY = "overlaparr.findings.view";

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "posters" ? "posters" : "table";
  } catch {
    return "table";
  }
}

function storeView(view: View) {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    // Storage blocked (private mode etc.): the choice just isn't remembered.
  }
}

export function Findings() {
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<View>(readView);
  const query = apiQuery(params);
  const findings = useQuery({
    queryKey: [...queryKeys.findings, query],
    queryFn: () => api.get<Page<Finding>>(`/findings?${query}`),
    placeholderData: keepPreviousData,
  });
  // Instance and server names for the filters (same query the Layout polls).
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<Dashboard>("/dashboard"),
  });

  // Selection belongs to one result set: changing filters, sort or page clears it.
  const [selection, setSelection] = useState({ query, ids: new Set<number>() });
  const selected = selection.query === query ? selection.ids : new Set<number>();
  const select = (ids: Set<number>) => setSelection({ query, ids });
  const onToggle = (id: number) => {
    const next = new Set(selected);
    if (!next.delete(id)) next.add(id);
    select(next);
  };
  const onToggleAll = (ids: number[], checked: boolean) => {
    const next = new Set(selected);
    for (const id of ids) {
      if (checked) next.add(id);
      else next.delete(id);
    }
    select(next);
  };

  const update = (updates: Record<string, string | null>) =>
    setParams(withParams(params, updates));
  const { sort, order } = currentSort(params);
  const onSort = (key: FindingSort) => {
    const desc = key === sort && order === "asc";
    update({ sort: key === "title" ? null : key, order: desc ? "desc" : null });
  };
  const changeView = (next: View) => {
    setView(next);
    storeView(next);
  };

  const items = findings.data?.items ?? [];
  const total = findings.data?.total ?? 0;
  const selectionProps = { selected, onToggle, onToggleAll };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-slate-100">Findings</h1>
        <ViewToggle view={view} onChange={changeView} />
      </div>
      <FiltersBar params={params} dashboard={dashboard.data} onChange={update} />
      {selected.size > 0 && <BulkBar ids={[...selected]} onDone={() => select(new Set())} />}
      {findings.isPending ? (
        <p className="text-slate-400">Loading…</p>
      ) : findings.isError ? (
        <p role="alert" className="text-red-300">
          {findings.error instanceof ApiError
            ? findings.error.detail
            : "Could not load findings"}
        </p>
      ) : items.length === 0 ? (
        <p className="text-slate-400">No findings match these filters.</p>
      ) : view === "table" ? (
        <FindingsTable items={items} sort={sort} order={order} onSort={onSort} {...selectionProps} />
      ) : (
        <FindingsCards items={items} total={total} {...selectionProps} />
      )}
      <Pager
        page={currentPage(params)}
        total={total}
        onPage={(p) => update({ page: p > 1 ? String(p) : null })}
      />
    </div>
  );
}

function ViewToggle({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  const button = (value: View, label: string) => (
    <button
      type="button"
      aria-pressed={view === value}
      onClick={() => onChange(value)}
      className={`px-3 py-1 text-sm ${
        view === value ? "bg-accent text-white" : "text-slate-300 hover:bg-slate-700"
      }`}
    >
      {label}
    </button>
  );
  return (
    <div role="group" aria-label="View" className="flex overflow-hidden rounded border border-slate-600">
      {button("table", "Table")}
      {button("posters", "Posters")}
    </div>
  );
}

function Pager({
  page,
  total,
  onPage,
}: {
  page: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (total === 0) return null;
  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  const nav = "rounded px-3 py-1 hover:bg-slate-700 disabled:opacity-40";
  return (
    <nav aria-label="Pages" className="flex items-center justify-end gap-3 text-sm text-slate-300">
      <span>
        {first}–{last} of {total}
      </span>
      <button type="button" className={nav} disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <button
        type="button"
        className={nav}
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        Next
      </button>
    </nav>
  );
}
