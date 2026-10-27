// Thin fetch wrapper for the Overlaparr API. Paths are relative to /api/v1,
// e.g. api.get<Dashboard>("/dashboard").

export const API_BASE = "/api/v1";

/** Routes whose 401s are expected answers, not an expired session. */
const AUTH_EXEMPT = ["/auth/", "/setup/"];

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called on a 401 from any route except auth/setup (the router sends the user to /login). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function errorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d as { msg?: unknown } | null)?.msg)
      .filter((m): m is string => typeof m === "string" && m !== "");
    if (msgs.length) return msgs.join("; ");
  }
  return fallback;
}

function parse(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  const init: RequestInit = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (res.status === 204) return undefined as T;
  const data = parse(await res.text());
  if (!res.ok) {
    if (res.status === 401 && !AUTH_EXEMPT.some((p) => path.startsWith(p))) {
      unauthorizedHandler?.();
    }
    throw new ApiError(res.status, errorDetail(data, res.statusText || `HTTP ${res.status}`));
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T = void>(path: string, body?: unknown) => request<T>("DELETE", path, body),
};

