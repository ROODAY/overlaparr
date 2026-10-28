import type { Dashboard, Finding, ScanProgress, ScanStatus } from "../api/types";

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

export function findingFixture(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 1,
    instance: { id: "i-2", name: "Radarr", type: "radarr" },
    arr_id: 10,
    kind: "movie",
    type: "on_disk",
    title: "Heat",
    year: 1995,
    poster_url: null,
    yours: { resolution: "1080p", size: 8_589_934_592 },
    elsewhere: [
      {
        server_id: "m-a",
        label: "Alpha",
        resolution: "2160p",
        hdr: true,
        stale: false,
        ignored: false,
      },
    ],
    badges: {
      coverage: "full",
      covered_count: 1,
      relevant_count: 1,
      best_quality: "better",
      edition: "same",
      has_number_matches: false,
    },
    review_state: "new",
    lifecycle: "active",
    automation_hold: false,
    arr_url: null,
    ...overrides,
  };
}
