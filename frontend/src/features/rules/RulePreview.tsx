import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { RulePreview as RulePreviewData } from "../../api/types";
import type { RuleDraft } from "./defaults";
import { actionVerb } from "./labels";

export const PREVIEW_DEBOUNCE_MS = 400;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/**
 * "Would <action> N items now" for the draft, re-evaluated 400 ms after the last edit.
 * Each draft is its own query, so a slow response for an older draft never replaces
 * the latest one; the previous result stays visible (dimmed) while the next loads.
 */
export function RulePreview({ draft, ruleId }: { draft: RuleDraft; ruleId?: string }) {
  const body = JSON.stringify({ rule: { ...draft, id: ruleId } });
  const debounced = useDebounced(body, PREVIEW_DEBOUNCE_MS);
  const preview = useQuery({
    queryKey: ["rules", "preview", debounced],
    queryFn: () => api.post<RulePreviewData>("/rules/preview", JSON.parse(debounced)),
    placeholderData: keepPreviousData,
  });
  const updating = debounced !== body || preview.isPlaceholderData;

  return (
    <section aria-label="Preview" className="mt-4 rounded-lg bg-slate-800 p-3 text-sm">
      {preview.isPending ? (
        <p className="text-slate-400">Checking current findings…</p>
      ) : preview.isError ? (
        <p role="alert" className="text-red-300">
          Preview failed:{" "}
          {preview.error instanceof ApiError ? preview.error.detail : "could not reach Overlaparr"}
        </p>
      ) : (
        <div className={updating ? "opacity-60" : undefined}>
          <p className="text-slate-200">
            Preview against current findings:{" "}
            <strong>
              Would {actionVerb(draft)} {preview.data.count}{" "}
              {preview.data.count === 1 ? "item" : "items"} now
            </strong>
          </p>
          {preview.data.items.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-accent hover:text-accent-hover">
                Show list
              </summary>
              <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto text-slate-300">
                {preview.data.items.map((item) => (
                  <li key={item.finding_id}>
                    <Link to={`/findings/${item.finding_id}`} className="hover:underline">
                      {item.title ?? `Finding ${item.finding_id}`}
                      {item.episodes > 0 &&
                        ` (${item.episodes} ${item.episodes === 1 ? "episode" : "episodes"})`}
                    </Link>
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="mt-2 text-xs text-slate-400">
            Ignored findings, findings on automation hold and copies from offline servers are
            skipped. Scheduled runs also wait for the configured number of scans.
          </p>
        </div>
      )}
    </section>
  );
}
