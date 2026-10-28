import type { FindingSort, SortOrder } from "../../api/types";

/** URL query names, identical to GET /findings query params. */
export const FILTER_KEYS = [
  "type",
  "coverage",
  "quality",
  "edition",
  "match_method",
  "instance",
  "server",
  "review_state",
  "lifecycle",
  "q",
] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

const API_KEYS = [...FILTER_KEYS, "sort", "order", "page"] as const;

export const PAGE_SIZE = 50;

/** The GET /findings query string for the page's URL params (unknown keys dropped). */
export function apiQuery(params: URLSearchParams): string {
  const out = new URLSearchParams();
  for (const key of API_KEYS) {
    const value = params.get(key);
    if (value) out.set(key, value);
  }
  out.set("page_size", String(PAGE_SIZE));
  return out.toString();
}

export function currentSort(params: URLSearchParams): { sort: FindingSort; order: SortOrder } {
  return {
    sort: (params.get("sort") as FindingSort | null) ?? "title",
    order: params.get("order") === "desc" ? "desc" : "asc",
  };
}

export function currentPage(params: URLSearchParams): number {
  const page = Number(params.get("page"));
  return Number.isInteger(page) && page > 1 ? page : 1;
}

/** Copy of params with the updates applied; empty values are removed, and page resets. */
export function withParams(
  params: URLSearchParams,
  updates: Record<string, string | null>,
): URLSearchParams {
  const next = new URLSearchParams(params);
  if (!("page" in updates)) next.delete("page");
  for (const [key, value] of Object.entries(updates)) {
    if (value) next.set(key, value);
    else next.delete(key);
  }
  return next;
}
