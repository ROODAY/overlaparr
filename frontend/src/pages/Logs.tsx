import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { API_BASE, api, ApiError } from "../api/client";
import type { LogEntry, LogQueryLevel } from "../api/types";
import { Badge, type BadgeVariant } from "../components/Badge";
import { formatDateTime } from "../lib/format";

const LEVELS: LogQueryLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const LIMIT = 500;
const POLL_MS = 5000;

const SELECT =
  "rounded border border-slate-600 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-accent focus:outline-none";

function levelVariant(level: string | null): BadgeVariant {
  if (level === "WARNING") return "caution";
  if (level === "ERROR" || level === "CRITICAL") return "bad";
  return "neutral";
}

/** formatDateTime, or the raw string when it is not a parsable date. */
function formatTs(ts: string | null): string {
  if (!ts) return "";
  return Number.isNaN(new Date(ts).getTime()) ? ts : formatDateTime(ts);
}

export function Logs() {
  const levelId = useId();
  const [level, setLevel] = useState<LogQueryLevel>("INFO");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const logs = useQuery({
    queryKey: ["logs", level],
    queryFn: () => api.get<LogEntry[]>(`/logs?level=${level}&limit=${LIMIT}`),
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-100">Logs</h1>
          <p className="mt-1 text-sm text-slate-400">
            Recent application log entries, newest first.
          </p>
        </div>
        <a
          href={`${API_BASE}/logs/download`}
          className="rounded border border-slate-600 px-4 py-2 text-sm text-slate-200 hover:bg-slate-700"
        >
          Download
        </a>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor={levelId} className="text-xs text-slate-400">
            Level
          </label>
          <select
            id={levelId}
            value={level}
            onChange={(e) => setLevel(e.target.value as LogQueryLevel)}
            className={SELECT}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-2 py-1 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          Auto-refresh
        </label>
      </div>

      {logs.isPending ? (
        <p className="mt-6 text-slate-400">Loading…</p>
      ) : logs.isError ? (
        <div className="mt-6">
          <p role="alert" className="text-red-300">
            {logs.error instanceof ApiError ? logs.error.detail : "Could not load logs"}
          </p>
          <button
            type="button"
            onClick={() => void logs.refetch()}
            className="mt-2 rounded border border-slate-600 px-3 py-1 text-sm text-slate-200 hover:bg-slate-700"
          >
            Retry
          </button>
        </div>
      ) : logs.data.length === 0 ? (
        <p className="mt-6 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
          No log entries
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Level</th>
                <th className="px-3 py-2">Logger</th>
                <th className="px-3 py-2">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/60">
              {logs.data.map((entry, i) => (
                // Log lines have no id; position in this newest-first snapshot is the key.
                <tr key={i} className="align-top">
                  <td className="whitespace-nowrap px-3 py-2 text-slate-300">{formatTs(entry.ts)}</td>
                  <td className="px-3 py-2">
                    {entry.level && <Badge variant={levelVariant(entry.level)}>{entry.level}</Badge>}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-400">{entry.logger}</td>
                  <td className="px-3 py-2 text-slate-200">
                    <div className="whitespace-pre-wrap break-words">{entry.msg}</div>
                    {entry.exc && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-slate-400">Exception</summary>
                        <pre className="mt-1 overflow-x-auto text-xs text-red-200">{entry.exc}</pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
