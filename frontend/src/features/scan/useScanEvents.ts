import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_BASE } from "../../api/client";
import { queryKeys } from "../../api/queryClient";
import type { ScanPhase, ScanProgress, ScanStatus } from "../../api/types";

/** The parts of EventSource the hook uses (jsdom has none, so tests pass a fake). */
export interface EventSourceLike {
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  close(): void;
}

export interface ScanEventsOptions {
  /** Opens the progress stream; returning null (or no EventSource support) means poll only. */
  createEventSource?: (url: string) => EventSourceLike | null;
  /** Poll interval while the stream is down. */
  pollMs?: number;
}

export interface ScanEvents {
  running: boolean;
  phase: ScanPhase | null;
  detail: string | null;
  done: number;
  total: number;
  /** Re-reads GET /scan/status now (e.g. right after starting a scan). */
  refresh: () => Promise<void>;
}

export const EVENTS_URL = `${API_BASE}/scan/events`;
const POLL_MS = 3000;

const IDLE: ScanProgress = { phase: null, detail: null, done: 0, total: 0, running: false };

function defaultEventSource(url: string): EventSourceLike | null {
  return typeof EventSource === "undefined" ? null : new EventSource(url);
}

/**
 * Live scan progress from the SSE stream, falling back to polling /scan/status while the
 * stream is down. The stream can drop events for a slow client (even the final one), so
 * /scan/status is re-read whenever a scan ends, on reconnect and while polling, and its
 * answer replaces whatever the stream said last.
 */
export function useScanEvents({
  createEventSource = defaultEventSource,
  pollMs = POLL_MS,
}: ScanEventsOptions = {}): ScanEvents {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<ScanProgress>(IDLE);
  const runningRef = useRef(false);
  const mountedRef = useRef(true);

  const apply = useCallback(
    (next: ScanProgress) => {
      const wasRunning = runningRef.current;
      runningRef.current = next.running;
      setProgress(next);
      if (wasRunning && !next.running) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
        void queryClient.invalidateQueries({ queryKey: queryKeys.scanStatus });
      }
      return wasRunning && !next.running;
    },
    [queryClient],
  );

  const refresh = useCallback(async () => {
    try {
      const status = await api.get<ScanStatus>("/scan/status");
      if (mountedRef.current) apply(status.progress);
    } catch {
      // Unreachable for now; the next poll or stream event will catch up.
    }
  }, [apply]);

  // The stream effect must not restart when the factory identity changes between renders.
  const factoryRef = useRef(createEventSource);
  useEffect(() => {
    factoryRef.current = createEventSource;
  });

  useEffect(() => {
    mountedRef.current = true;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let opened = false;

    const startPolling = () => {
      if (pollTimer !== null) return;
      void refresh();
      pollTimer = setInterval(() => void refresh(), pollMs);
    };
    const stopPolling = () => {
      if (pollTimer !== null) clearInterval(pollTimer);
      pollTimer = null;
    };

    let source: EventSourceLike | null = null;
    try {
      source = factoryRef.current(EVENTS_URL);
    } catch {
      source = null;
    }

    if (source === null) {
      startPolling();
    } else {
      source.onopen = () => {
        stopPolling();
        // Events sent while disconnected are lost: catch up from the status endpoint.
        if (opened) void refresh();
        opened = true;
      };
      source.onmessage = (ev) => {
        let next: ScanProgress;
        try {
          next = { ...IDLE, ...(JSON.parse(ev.data as string) as ScanProgress) };
        } catch {
          return;
        }
        // A finished scan may have been followed by events this client never got.
        if (apply(next)) void refresh();
      };
      // EventSource reconnects by itself; poll until it is back (or for good if it gave up).
      source.onerror = startPolling;
    }

    return () => {
      mountedRef.current = false;
      stopPolling();
      if (source !== null) {
        source.onopen = source.onmessage = source.onerror = null;
        source.close();
      }
    };
  }, [apply, refresh, pollMs]);

  return {
    running: progress.running,
    phase: progress.phase,
    detail: progress.detail,
    done: progress.done,
    total: progress.total,
    refresh,
  };
}
