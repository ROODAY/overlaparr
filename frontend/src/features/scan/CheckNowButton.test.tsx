import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { ScanProgress } from "../../api/types";
import { FakeEventSource, fakeEventSourceFactory } from "../../test/fakeEventSource";
import { IDLE, scanStatus } from "../../test/fixtures";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { CheckNowButton } from "./CheckNowButton";

const OPTIONS = { createEventSource: fakeEventSourceFactory, pollMs: 20 };
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
  FakeEventSource.reset();
  statusCalls = 0;
  status = IDLE;
  server.use(
    http.get("*/api/v1/scan/status", () => {
      statusCalls += 1;
      return HttpResponse.json(scanStatus(status));
    }),
  );
});

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
    renderButton();
    const es = FakeEventSource.latest;
    act(() => es.open());
    act(() => es.fail());
    await waitFor(() => expect(statusCalls).toBeGreaterThanOrEqual(1));
    act(() => es.open());
    const afterReconnect = statusCalls;
    await waitFor(() => expect(statusCalls).toBe(afterReconnect + 1));
    // No further polls once the stream is back.
    await new Promise((r) => setTimeout(r, 80));
    expect(statusCalls).toBe(afterReconnect + 1);
  });

  it("closes the stream on unmount", () => {
    const { unmount } = renderButton();
    const es = FakeEventSource.latest;
    unmount();
    expect(es.closed).toBe(true);
  });
});
