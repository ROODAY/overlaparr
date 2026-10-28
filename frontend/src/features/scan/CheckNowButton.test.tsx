import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ScanProgress } from "../../api/types";
import { FakeEventSource, fakeEventSourceFactory } from "../../test/fakeEventSource";
import { IDLE, scanStatus } from "../../test/fixtures";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { CheckNowButton } from "./CheckNowButton";

const OPTIONS = { createEventSource: fakeEventSourceFactory, pollMs: 20, safetyPollMs: 60_000 };
const INDEXING: ScanProgress = {
  phase: "indexing",
  detail: "Friend",
  done: 1,
  total: 3,
  running: true,
};

let statusCalls: number;
let status: ScanProgress;

beforeEach(() => {
  statusCalls = 0;
  status = IDLE;
  server.use(
    http.get("*/api/v1/scan/status", () => {
      statusCalls += 1;
      return HttpResponse.json(scanStatus(status));
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

/** Fakes timers before mount so the hook's intervals run on the fake clock. */
function useFakeClock() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
}

function renderButton() {
  return renderWithProviders(<CheckNowButton scanOptions={OPTIONS} />);
}

describe("CheckNowButton", () => {
  it("is enabled when idle", () => {
    renderButton();
    act(() => FakeEventSource.latest.emit(IDLE));
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
    expect(FakeEventSource.latest.url).toBe("/api/v1/scan/events");
  });

  it("is disabled and shows the phase while a scan runs", () => {
    renderButton();
    act(() => FakeEventSource.latest.emit(INDEXING));
    expect(screen.getByRole("button", { name: /Scanning/ })).toBeDisabled();
    expect(screen.getByText("Indexing servers: Friend (1/3)")).toBeInTheDocument();
  });

  it("starts a scan", async () => {
    let posted = false;
    server.use(
      http.post("*/api/v1/scan", () => {
        posted = true;
        status = INDEXING;
        return HttpResponse.json({ scan_id: 13 }, { status: 202 });
      }),
    );
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    expect(posted).toBe(true);
    // The status is re-read straight away rather than waiting for the stream.
    expect(await screen.findByRole("button", { name: /Scanning/ })).toBeDisabled();
  });

  it("toasts when a scan is already running", async () => {
    server.use(
      http.post("*/api/v1/scan", () =>
        HttpResponse.json({ detail: "Scan already running" }, { status: 409 }),
      ),
    );
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Scan already running"),
    );
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });

  it("re-reads the status when the stream reports the scan finished", async () => {
    renderButton();
    act(() => FakeEventSource.latest.emit(INDEXING));
    act(() => FakeEventSource.latest.emit({ ...IDLE, phase: "done", status: "ok" }));
    await waitFor(() => expect(statusCalls).toBe(1));
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });

  it("falls back to polling on a stream error and trusts the poll", async () => {
    renderButton();
    act(() => FakeEventSource.latest.emit(INDEXING));
    status = { ...INDEXING, phase: "matching", detail: null, done: 0, total: 0 };
    act(() => FakeEventSource.latest.fail());
    expect(await screen.findByText("Matching")).toBeInTheDocument();
    // The stream missed the end; the poll says the scan is over, so the button is not stuck.
    status = IDLE;
    expect(await screen.findByRole("button", { name: "Check now" })).toBeEnabled();
  });

  it("stops polling and re-reads the status when the stream reconnects", async () => {
    useFakeClock();
    renderWithProviders(<CheckNowButton scanOptions={{ ...OPTIONS, pollMs: 1000 }} />);
    const es = FakeEventSource.latest;
    act(() => es.open());
    act(() => es.fail()); // polls at once, then every second
    await waitFor(() => expect(statusCalls).toBe(1));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    await waitFor(() => expect(statusCalls).toBe(2));
    act(() => es.open()); // reconnected: one catch-up read
    await waitFor(() => expect(statusCalls).toBe(3));
    // No further polls once the stream is back.
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(statusCalls).toBe(3);
  });

  it("re-reads the status while running even if the stream stays silent", async () => {
    useFakeClock();
    renderWithProviders(<CheckNowButton scanOptions={{ ...OPTIONS, safetyPollMs: 1000 }} />);
    const es = FakeEventSource.latest;
    act(() => es.open());
    act(() => es.emit(INDEXING));
    expect(screen.getByRole("button", { name: /Scanning/ })).toBeDisabled();
    // The final "not running" event is dropped; nothing else arrives on the stream.
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(await screen.findByRole("button", { name: "Check now" })).toBeEnabled();
    expect(statusCalls).toBe(1);
    // The safety poll stops once idle.
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(statusCalls).toBe(1);
  });

  it("ignores a status response older than a newer stream event", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let staleSent = false;
    server.use(
      http.get("*/api/v1/scan/status", async () => {
        await gate;
        staleSent = true;
        return HttpResponse.json(scanStatus(IDLE));
      }),
    );
    renderButton();
    const es = FakeEventSource.latest;
    act(() => es.fail()); // starts a poll, held at the gate
    act(() => es.open());
    act(() => es.emit(INDEXING));
    release();
    await waitFor(() => expect(staleSent).toBe(true));
    // Let the client read and apply (or drop) the stale response.
    await act(() => new Promise((r) => setTimeout(r, 0)));
    expect(screen.getByRole("button", { name: /Scanning/ })).toBeDisabled();
    expect(screen.getByText("Indexing servers: Friend (1/3)")).toBeInTheDocument();
  });

  it("closes the stream on unmount", () => {
    const { unmount } = renderButton();
    const es = FakeEventSource.latest;
    unmount();
    expect(es.closed).toBe(true);
  });
});
