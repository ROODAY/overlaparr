import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LogEntry } from "../api/types";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { Logs } from "./Logs";

let entries: LogEntry[];
let queries: URLSearchParams[];

beforeEach(() => {
  entries = [
    {
      ts: "2026-10-27T10:00:00Z",
      level: "ERROR",
      logger: "overlaparr.scan",
      msg: "Scan failed",
      exc: "Traceback (most recent call last):\n  boom",
    },
    { ts: "not a date", level: "INFO", logger: "overlaparr.app", msg: "Started", exc: null },
  ];
  queries = [];
  server.use(
    http.get("*/api/v1/logs", ({ request }) => {
      queries.push(new URL(request.url).searchParams);
      return HttpResponse.json(entries);
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

function row(name: RegExp) {
  return within(screen.getByRole("row", { name }));
}

describe("Logs", () => {
  it("renders entries with level badges and exceptions", async () => {
    renderWithProviders(<Logs />, { route: "/logs" });
    expect(await screen.findByRole("heading", { name: "Logs" })).toBeInTheDocument();
    await screen.findByRole("row", { name: /Scan failed/ });

    const failed = row(/Scan failed/);
    expect(failed.getByText("ERROR")).toBeInTheDocument();
    expect(failed.getByText("overlaparr.scan")).toBeInTheDocument();
    expect(failed.getByText("Exception")).toBeInTheDocument();
    expect(failed.getByText(/boom/)).toBeInTheDocument();

    expect(row(/Started/).getByText("not a date")).toBeInTheDocument();
    expect(queries[0].get("level")).toBe("INFO");
    expect(queries[0].get("limit")).toBe("500");
  });

  it("level filter changes the request param", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Logs />, { route: "/logs" });
    await screen.findByRole("row", { name: /Scan failed/ });
    await user.selectOptions(screen.getByLabelText("Level"), "WARNING");
    await waitFor(() => expect(queries[queries.length - 1].get("level")).toBe("WARNING"));
  });

  it("auto-refresh polls every 5 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWithProviders(<Logs />, { route: "/logs" });
    await screen.findByRole("row", { name: /Scan failed/ });
    expect(queries).toHaveLength(1);

    // Off by default: no polling.
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(queries).toHaveLength(1);

    await user.click(screen.getByRole("checkbox", { name: "Auto-refresh" }));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() => expect(queries.length).toBeGreaterThanOrEqual(2));
  });

  it("download link points at the zip endpoint", async () => {
    renderWithProviders(<Logs />, { route: "/logs" });
    expect(await screen.findByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/v1/logs/download",
    );
  });

  it("shows the empty state", async () => {
    entries = [];
    renderWithProviders(<Logs />, { route: "/logs" });
    expect(await screen.findByText("No log entries")).toBeInTheDocument();
  });

  it("shows an error with Retry", async () => {
    server.use(
      http.get("*/api/v1/logs", () => HttpResponse.json({ detail: "Nope" }, { status: 400 })),
    );
    renderWithProviders(<Logs />, { route: "/logs" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Nope");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
