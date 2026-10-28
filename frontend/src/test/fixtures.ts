import type { Dashboard, ScanProgress, ScanStatus } from "../api/types";

export const IDLE: ScanProgress = {
  phase: null,
  detail: null,
  done: 0,
  total: 0,
  running: false,
};

export function scanStatus(progress: Partial<ScanProgress> = {}): ScanStatus {
  return { progress: { ...IDLE, ...progress }, runs: [] };
}

export function dashboardFixture(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    counts: {
      by_type: { wanted_elsewhere: 4, on_disk: 7 },
      by_coverage: { full: 9, partial: 2 },
      by_state: { new: 5, reviewed: 3, ignored: 2, actioned: 1 },
    },
    reclaimable_bytes: 1288490189, // 1.2 GB
    lost: 3,
    last_scan: {
      id: 12,
      started_at: "2026-10-26T10:00:00Z",
      finished_at: "2026-10-26T10:05:00Z",
      status: "ok",
      trigger: "schedule",
      error: null,
      summary: {},
    },
    next_scan: "2026-10-27T10:00:00Z",
    servers: [
      { id: "m-a", label: "Alpha", reachability: "online", last_indexed_at: "2026-10-26T10:01:00Z" },
      { id: "m-b", label: "Bravo", reachability: "stale", last_indexed_at: "2026-10-20T08:00:00Z" },
    ],
    instances: [
      { id: "i-1", name: "Sonarr", ok: true },
      { id: "i-2", name: "Radarr 4K", ok: false },
      { id: "i-3", name: "Radarr", ok: null },
    ],
    config_error: null,
    ...overrides,
  };
}
