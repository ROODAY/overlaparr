import type {
  ActivityEntry,
  Dashboard,
  Finding,
  FindingDetail,
  Instance,
  RuleWithRun,
  ScanProgress,
  ScanStatus,
} from "../api/types";

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

export function activityFixture(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    id: 100,
    ts: "2026-10-27T10:00:00Z",
    actor: "ui",
    rule_id: null,
    rule_name: null,
    scan_run_id: null,
    instance_id: "i-2",
    instance_name: "Radarr",
    finding_id: 1,
    arr_id: 10,
    item_title: "Heat",
    action: "unmonitor",
    scope: { scope: "item" },
    prior_state: {},
    result: "ok",
    message: null,
    undoable: true,
    undone_at: null,
    undo_of_id: null,
    can_undo: true,
    ...overrides,
  };
}

export function movieDetailFixture(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return {
    ...findingFixture(),
    copies: [
      {
        server_id: "m-a",
        label: "Alpha",
        resolution: "2160p",
        hdr: true,
        codec: "hevc",
        edition: "Director's Cut",
        size: 21_474_836_480, // 20.0 GB
        quality: "better",
        edition_cmp: "different",
        stale: false,
        ignored: false,
      },
      {
        server_id: "m-b",
        label: "Bravo",
        resolution: "720p",
        hdr: false,
        codec: "h264",
        edition: null,
        size: null,
        quality: "worse",
        edition_cmp: "same",
        stale: true,
        ignored: false,
      },
    ],
    history: [],
    ...overrides,
  };
}

const GB = 1024 ** 3;

/**
 * Severance: S1 fully covered by bob and carol (E2 at carol matched by number),
 * S2 has one episode at carol and one nowhere. bob's S1E2 copy is stale.
 */
export function seriesDetailFixture(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return {
    ...findingFixture({
      id: 5,
      kind: "series",
      title: "Severance",
      year: 2022,
      instance: { id: "i-1", name: "Sonarr", type: "sonarr" },
      arr_id: 50,
      yours: { resolution: "1080p", size: 4.4 * GB },
      elsewhere: [
        { server_id: "m-b", label: "bob", resolution: "2160p", hdr: true, stale: false, ignored: false },
        { server_id: "m-c", label: "carol", resolution: "1080p", hdr: false, stale: false, ignored: false },
      ],
      badges: {
        coverage: "partial",
        covered_count: 3,
        relevant_count: 4,
        best_quality: "better",
        edition: "unknown",
        has_number_matches: true,
      },
    }),
    seasons: [
      { season: 1, relevant: 2, covered: 2, servers: ["bob", "carol"] },
      { season: 2, relevant: 2, covered: 1, servers: ["carol"] },
    ],
    episodes: [
      {
        episode_arr_id: 101,
        season: 1,
        episode: 1,
        title: "Good News About Hell",
        yours: { state: "have", resolution: "1080p", size: 2.1 * GB },
        copies: {
          "m-b": { resolution: "2160p", hdr: true, matched_by: "id", stale: false, quality: "better" },
          "m-c": { resolution: "1080p", hdr: false, matched_by: "id", stale: false, quality: "same" },
        },
      },
      {
        episode_arr_id: 102,
        season: 1,
        episode: 2,
        title: "Half Loop",
        yours: { state: "have", resolution: "1080p", size: 2.3 * GB },
        copies: {
          "m-b": { resolution: "2160p", hdr: true, matched_by: "id", stale: true, quality: "better" },
          "m-c": { resolution: "720p", hdr: false, matched_by: "number", stale: false, quality: "worse" },
        },
      },
      {
        episode_arr_id: 201,
        season: 2,
        episode: 1,
        title: "Hello, Ms. Cobel",
        yours: { state: "wanted", resolution: null, size: null },
        copies: {
          "m-c": { resolution: "1080p", hdr: false, matched_by: "id", stale: false, quality: "unknown" },
        },
      },
      {
        episode_arr_id: 202,
        season: 2,
        episode: 2,
        title: "Goodbye, Mrs. Selvig",
        yours: { state: "wanted", resolution: null, size: null },
        copies: {},
      },
    ],
    history: [],
    ...overrides,
  };
}

export function ruleFixture(overrides: Partial<RuleWithRun> = {}): RuleWithRun {
  return {
    id: "r-1",
    name: "Unmonitor wanted",
    enabled: false,
    action: "unmonitor",
    delete_mode: "files",
    add_exclusion: false,
    delete_confirmed: false,
    finding_types: ["wanted_elsewhere"],
    instances: [],
    servers: [],
    min_servers: 1,
    coverage: "full",
    quality: ["better", "same"],
    edition: ["same"],
    match_methods: ["id"],
    wait_scans: 2,
    dry_run_next: false,
    last_run: null,
    last_affected: 0,
    ...overrides,
  };
}

export function instanceFixture(overrides: Partial<Instance> = {}): Instance {
  return {
    id: "i-1",
    name: "Sonarr",
    type: "sonarr",
    url: "http://sonarr:8989",
    api_key: "********",
    enabled: true,
    loss_handling: { remonitor: false, remove_exclusion: false, quality_profile_id: null, search: false },
    ...overrides,
  };
}
