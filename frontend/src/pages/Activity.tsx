import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type {
  ActionName,
  ActionResult,
  ActivityEntry,
  ActivityQuery,
  Actor,
  Instance,
  Page,
  RuleWithRun,
} from "../api/types";
import { Badge, type BadgeVariant } from "../components/Badge";
import { useToast } from "../components/useToast";
import {
  actionLabel,
  FILES_GONE,
  isDeleteRow,
  scopeText,
  undoLabel,
} from "../features/activity/labels";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;

const ACTIONS: (ActionName | "clear_hold")[] = [
  "unmonitor",
  "delete",
  "exclude",
  "tag",
  "ignore",
  "ignore_server",
  "mark_reviewed",
  "unignore",
  "mark_new",
  "clear_hold",
];

const ACTOR_LABEL: Record<Actor, string> = { ui: "UI", auto: "Auto" };
const ACTOR_VARIANT: Record<Actor, BadgeVariant> = { ui: "neutral", auto: "caution" };
const RESULT_LABEL: Record<ActionResult, string> = { ok: "OK", error: "Error", dry_run: "Dry run" };
const RESULT_VARIANT: Record<ActionResult, BadgeVariant> = {
  ok: "good",
  error: "bad",
  dry_run: "neutral",
};

const SELECT =
  "rounded border border-slate-600 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-accent focus:outline-none";
const SMALL_BUTTON = "rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:opacity-50";

interface Filters {
  actor: string;
  action: string;
  instance: string;
  rule: string;
  /** YYYY-MM-DD from the date inputs. */
  from: string;
  to: string;
}

const NO_FILTERS: Filters = { actor: "", action: "", instance: "", rule: "", from: "", to: "" };

/** Local-time bound of a YYYY-MM-DD day as an ISO string (start or end of the day). */
function dayBound(day: string, end: boolean): string | undefined {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return undefined;
  const date = end ? new Date(y, m - 1, d, 23, 59, 59, 999) : new Date(y, m - 1, d, 0, 0, 0, 0);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function toQuery(filters: Filters, page: number): string {
  const q: ActivityQuery = {
    actor: (filters.actor || undefined) as Actor | undefined,
    action: filters.action || undefined,
    instance: filters.instance || undefined,
    rule: filters.rule || undefined,
    since: filters.from ? dayBound(filters.from, false) : undefined,
    until: filters.to ? dayBound(filters.to, true) : undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(q)) {
    if (value !== undefined) params.set(key, String(value));
  }
  return params.toString();
}

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : "could not reach Overlaparr";
}

export function Activity() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [page, setPage] = useState(1);
  const query = toQuery(filters, page);

  const activity = useQuery({
    queryKey: ["activity", query],
    queryFn: () => api.get<Page<ActivityEntry>>(`/activity?${query}`),
    placeholderData: keepPreviousData,
  });
  const instances = useQuery({
    queryKey: queryKeys.instances,
    queryFn: () => api.get<Instance[]>("/instances"),
  });
  const rules = useQuery({
    queryKey: queryKeys.rules,
    queryFn: () => api.get<RuleWithRun[]>("/rules"),
  });

  const undo = useMutation({
    mutationFn: (row: ActivityEntry) => api.post<ActivityEntry>(`/activity/${row.id}/undo`),
    onSuccess: (result, row) => {
      void queryClient.invalidateQueries({ queryKey: ["activity"] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.findings });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
      // A 200 row can still report the *arr call failed: only say "undone" for ok.
      toast(
        result.result === "ok"
          ? `Undone: ${actionLabel(row)}`
          : `Undo failed: ${result.message ?? "unknown error"}`,
        result.result === "ok" ? "info" : "error",
      );
    },
    onError: (e) => toast(`Could not undo: ${message(e)}`, "error"),
  });

  const update = (changes: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...changes }));
    setPage(1);
  };

  const total = activity.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-100">Activity</h1>
      <p className="mt-1 text-sm text-slate-400">
        Every action Overlaparr took, from the UI or automation rules.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <Select
          label="Actor"
          value={filters.actor}
          options={[
            ["ui", "UI"],
            ["auto", "Auto"],
          ]}
          onChange={(actor) => update({ actor })}
        />
        <Select
          label="Action"
          value={filters.action}
          options={ACTIONS.map((a) => [a, a])}
          onChange={(action) => update({ action })}
        />
        <Select
          label="Instance"
          value={filters.instance}
          options={(instances.data ?? []).map((i) => [i.id, i.name])}
          onChange={(instance) => update({ instance })}
        />
        <Select
          label="Rule"
          value={filters.rule}
          options={(rules.data ?? []).map((r) => [r.id, r.name])}
          onChange={(rule) => update({ rule })}
        />
        <DateInput label="From" value={filters.from} onChange={(from) => update({ from })} />
        <DateInput label="To" value={filters.to} onChange={(to) => update({ to })} />
      </div>

      {activity.isPending ? (
        <p className="mt-6 text-slate-400">Loading…</p>
      ) : activity.isError ? (
        <div className="mt-6">
          <p role="alert" className="text-red-300">
            {activity.error instanceof ApiError
              ? activity.error.detail
              : "Could not load activity"}
          </p>
          <button
            type="button"
            onClick={() => void activity.refetch()}
            className="mt-2 rounded border border-slate-600 px-3 py-1 text-sm text-slate-200 hover:bg-slate-700"
          >
            Retry
          </button>
        </div>
      ) : activity.data.items.length === 0 ? (
        <p className="mt-6 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
          No activity yet
        </p>
      ) : (
        <>
          <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
            <table className="w-full text-sm">
              <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Actor</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Item</th>
                  <th className="px-3 py-2">Instance</th>
                  <th className="px-3 py-2">Rule</th>
                  <th className="px-3 py-2">Result</th>
                  <th className="px-3 py-2">Message</th>
                  <th className="px-3 py-2">Undo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {activity.data.items.map((entry) => {
                  const scope = scopeText(entry.scope);
                  const deleteRow = isDeleteRow(entry);
                  return (
                    <tr key={entry.id} className="align-middle">
                      <td className="whitespace-nowrap px-3 py-2 text-slate-300">
                        {formatDateTime(entry.ts)}
                      </td>
                      <td className="px-3 py-2">
                        <Badge variant={ACTOR_VARIANT[entry.actor]}>{ACTOR_LABEL[entry.actor]}</Badge>
                      </td>
                      <td className="px-3 py-2 text-slate-200">
                        {actionLabel(entry)}
                        {scope && <span className="ml-1 text-slate-400">{scope}</span>}
                      </td>
                      <td className="px-3 py-2 text-slate-100">{entry.item_title}</td>
                      <td className="px-3 py-2 text-slate-300">{entry.instance_name}</td>
                      <td className="px-3 py-2 text-slate-300">{entry.rule_name ?? "—"}</td>
                      <td className="px-3 py-2">
                        <Badge variant={RESULT_VARIANT[entry.result] ?? "neutral"}>
                          {RESULT_LABEL[entry.result] ?? entry.result}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{entry.message}</td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {entry.can_undo ? (
                          <button
                            type="button"
                            aria-label={
                              deleteRow
                                ? `${undoLabel(entry)} for ${entry.item_title}`
                                : `Undo ${entry.action} for ${entry.item_title}`
                            }
                            disabled={undo.isPending}
                            onClick={() => undo.mutate(entry)}
                            className={SMALL_BUTTON}
                          >
                            {undoLabel(entry)}
                          </button>
                        ) : entry.undone_at ? (
                          <span className="text-slate-400">Undone</span>
                        ) : (
                          <span className="text-slate-500">Cannot be undone</span>
                        )}
                        {deleteRow && (
                          <span className="block text-xs font-medium text-red-300">{FILES_GONE}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <nav
            aria-label="Pages"
            className="mt-3 flex items-center justify-end gap-3 text-sm text-slate-300"
          >
            <span>
              Page {page} of {pages} ({total} total)
            </span>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="rounded px-3 py-1 hover:bg-slate-700 disabled:opacity-40"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={page >= pages}
              onClick={() => setPage(page + 1)}
              className="rounded px-3 py-1 hover:bg-slate-700 disabled:opacity-40"
            >
              Next
            </button>
          </nav>
        </>
      )}
    </div>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [value: string, label: string][];
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-slate-400">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={SELECT}>
        <option value="">All</option>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}

function DateInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-slate-400">
        {label}
      </label>
      <input
        id={id}
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={SELECT}
      />
    </div>
  );
}
