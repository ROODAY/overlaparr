// Shapes of the Overlaparr REST API (/api/v1). Mirrors backend/overlaparr/api/*.py.
// Datetimes are ISO-8601 strings (UTC). Secret values come back masked as MASK.

export const MASK = "********";

export type ISODateTime = string;

// ---------- Errors ----------

export interface ValidationErrorItem {
  loc: string[];
  msg: string;
}

/** Error body: a message, or (422) a list of validation errors. */
export interface ErrorBody {
  detail: string | ValidationErrorItem[];
}

// ---------- Setup / auth ----------

export interface SetupStatus {
  needs_user: boolean;
  plex_connected: boolean;
  home_server_set: boolean;
  instances_count: number;
}

export interface Credentials {
  username: string;
  password: string;
}

/** POST /setup/user and POST /auth/login. */
export interface LoginResult {
  username: string;
}

/** GET /auth/me. principal is a username, "api-key" or "local". */
export interface Me {
  principal: string;
}

export interface OkResult {
  ok: boolean;
}

// ---------- Plex ----------

/** POST /plex/pin */
export interface PlexPin {
  id: number;
  code: string;
  auth_url: string;
}

/** GET /plex/pin/{id}, PUT /plex/token, DELETE /plex/token */
export interface PlexConnected {
  connected: boolean;
}

export interface TokenIn {
  token: string;
}

// ---------- Servers ----------

export type Reachability = "online" | "degraded" | "offline" | "stale" | "partial";
export type ServerStatus = Reachability | "disabled";

/** GET /servers, POST /servers/refresh (list), PATCH /servers/{machine_id} (one). */
export interface ServerRow {
  machine_id: string;
  name: string;
  label: string;
  enabled: boolean;
  is_home: boolean;
  is_new: boolean;
  status: ServerStatus;
  reachability: Reachability;
  last_indexed_at: ISODateTime | null;
  last_attempt_at: ISODateTime | null;
  movie_count: number;
  show_count: number;
  unmatchable_count: number;
}

export interface HomeIn {
  machine_id: string;
}

/** PUT /servers/home */
export interface HomeResult {
  home_server: string;
  scan_id: number | null;
}

export interface ServerPatch {
  label?: string | null;
  enabled?: boolean | null;
}

// ---------- Instances ----------

export type InstanceType = "sonarr" | "radarr";

export interface LossHandling {
  remonitor: boolean;
  remove_exclusion: boolean;
  quality_profile_id: number | null;
  search: boolean;
}

/** GET /instances (list), POST/PUT (one). api_key is masked. */
export interface Instance {
  id: string;
  name: string;
  type: InstanceType;
  url: string;
  api_key: string;
  enabled: boolean;
  loss_handling: LossHandling;
}

/** POST /instances, PUT /instances/{id}. Send MASK as api_key on PUT to keep the stored key. */
export interface InstanceIn {
  name: string;
  type: InstanceType;
  url: string;
  api_key: string;
  enabled?: boolean;
  loss_handling?: Partial<LossHandling>;
}

/** POST /instances/test. With api_key = MASK and id set, the stored key is used. */
export interface InstanceTest {
  type: InstanceType;
  url: string;
  api_key: string;
  id?: string | null;
}

export type InstanceTestResult =
  | { ok: true; version: string | null }
  | { ok: false; error: string };

/** GET /instances/{id}/profiles */
export interface QualityProfile {
  id: number;
  name: string | null;
}

/** GET /instances/{id}/tags */
export interface ArrTag {
  id: number;
  label: string | null;
}

// ---------- Config sections (settings) ----------

export interface PlexConfig {
  token: string | null;
  home_server: string | null;
}

export interface ServerOverride {
  id: string;
  label: string | null;
  enabled: boolean;
}

export interface ScanConfig {
  cron: string;
  server_timeout_seconds: number;
  server_index_budget_seconds: number;
  page_size: number;
  stale_after_hours: number;
}

export interface TagsConfig {
  enabled: boolean;
  dupe: string;
  partial: string;
  ignore: string;
  per_server_tags: boolean;
}

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";

export interface LoggingConfig {
  level: LogLevel;
  max_bytes: number;
  backup_count: number;
}

export interface ActionLogConfig {
  retention_days: number | null;
}

export interface AuthConfig {
  local_bypass: boolean;
}

export interface NotificationsConfig {
  app_url: string | null;
  connections: NotificationConnection[];
}

/** GET/PUT /settings: the whole config (secrets masked) plus the on-disk config error. */
export interface Settings {
  plex: PlexConfig;
  servers: ServerOverride[];
  instances: Instance[];
  scan: ScanConfig;
  tags: TagsConfig;
  rules: Rule[];
  notifications: NotificationsConfig;
  logging: LoggingConfig;
  action_log: ActionLogConfig;
  auth: AuthConfig;
  config_error: string | null;
}

/** PUT /settings: partial sections, merged into the current values. */
export interface SettingsUpdate {
  scan?: Partial<ScanConfig>;
  tags?: Partial<TagsConfig>;
  logging?: Partial<LoggingConfig>;
  action_log?: Partial<ActionLogConfig>;
  auth?: Partial<AuthConfig>;
  notifications?: { app_url?: string | null };
}

/** GET /settings/cron-preview?cron= */
export interface CronPreview {
  next: ISODateTime[];
}

// ---------- Scan ----------

export type ScanPhase =
  | "servers"
  | "indexing"
  | "instances"
  | "matching"
  | "reconcile"
  | "hooks"
  | "done";

export type ScanRunStatus = "running" | "ok" | "failed";
export type ScanTrigger = "schedule" | "manual";

export interface ScanSummary {
  counts?: {
    new: number;
    lost: number;
    removed: number;
    resolved: number;
    updated: number;
    findings_active: number;
  };
  servers?: Record<string, ServerStatus>;
  instances?: Record<string, boolean>;
  hook_errors?: { hook: string; error: string }[];
}

export interface ScanRun {
  id: number;
  started_at: ISODateTime;
  finished_at: ISODateTime | null;
  status: ScanRunStatus;
  trigger: ScanTrigger;
  error: string | null;
  summary: ScanSummary;
}

/** Live progress (GET /scan/status .progress, and each SSE event of /scan/events). */
export interface ScanProgress {
  phase: ScanPhase | null;
  detail: string | null;
  done: number;
  total: number;
  running: boolean;
  scan_id?: number;
  status?: ScanRunStatus;
  error?: string | null;
}

/** POST /scan (202) */
export interface ScanStarted {
  scan_id: number;
}

/** GET /scan/status */
export interface ScanStatus {
  progress: ScanProgress;
  runs: ScanRun[];
}

// ---------- Findings ----------

export type FindingType = "wanted_elsewhere" | "on_disk";
export type FindingKind = "movie" | "series";
export type Coverage = "full" | "partial";
export type Quality = "better" | "same" | "worse" | "unknown";
export type EditionCmp = "same" | "different" | "unknown";
export type MatchMethod = "id" | "number";
export type ReviewState = "new" | "reviewed" | "ignored" | "actioned";
export type Lifecycle = "active" | "lost" | "removed" | "resolved";
export type ResolutionLabel = "SD" | "480p" | "576p" | "720p" | "1080p" | "2160p";

export interface FindingInstanceRef {
  id: string;
  name: string;
  type: InstanceType;
}

export interface FindingYours {
  resolution: ResolutionLabel | null;
  size: number | null;
}

/** One row per server in the list view's "elsewhere" column. */
export interface FindingElsewhere {
  server_id: string;
  label: string;
  resolution: ResolutionLabel | null;
  hdr: boolean;
  stale: boolean;
  ignored: boolean;
}

export interface FindingBadges {
  coverage: Coverage;
  covered_count: number;
  relevant_count: number;
  best_quality: Quality;
  edition: EditionCmp;
  has_number_matches: boolean;
}

export interface Finding {
  id: number;
  instance: FindingInstanceRef;
  arr_id: number;
  kind: FindingKind;
  type: FindingType;
  title: string;
  year: number | null;
  poster_url: string | null;
  yours: FindingYours;
  elsewhere: FindingElsewhere[];
  badges: FindingBadges;
  review_state: ReviewState;
  lifecycle: Lifecycle;
  automation_hold: boolean;
  arr_url: string | null;
}

export type FindingSort = "title" | "year" | "size" | "updated" | "servers";
export type SortOrder = "asc" | "desc";

/** Query of GET /findings. List filters are comma-separated; lifecycle "all" disables it. */
export interface FindingsQuery {
  type?: string;
  coverage?: string;
  quality?: string;
  edition?: string;
  match_method?: string;
  instance?: string;
  server?: string;
  review_state?: string;
  lifecycle?: string;
  q?: string;
  sort?: FindingSort;
  order?: SortOrder;
  page?: number;
  page_size?: number;
}

export interface Page<T> {
  items: T[];
  total: number;
}

/** Movie detail: one row per copy on another server. */
export interface FindingCopy {
  server_id: string;
  label: string;
  resolution: ResolutionLabel | null;
  hdr: boolean;
  codec: string | null;
  edition: string | null;
  size: number | null;
  quality: Quality;
  edition_cmp: EditionCmp;
  stale: boolean;
  ignored: boolean;
}

export interface SeasonSummary {
  season: number;
  relevant: number;
  covered: number;
  /** Server labels holding any episode of this season. */
  servers: string[];
}

export interface EpisodeCopy {
  resolution: ResolutionLabel | null;
  hdr: boolean;
  matched_by: MatchMethod;
  stale: boolean;
  quality: Quality;
}

export interface EpisodeRow {
  episode_arr_id: number;
  season: number;
  episode: number;
  title: string;
  yours: {
    state: "have" | "wanted";
    resolution: ResolutionLabel | null;
    size: number | null;
  };
  /** Keyed by server id. */
  copies: Record<string, EpisodeCopy>;
}

/** GET /findings/{id}: copies for movies; seasons + episodes for series. */
export interface FindingDetail extends Finding {
  copies?: FindingCopy[];
  seasons?: SeasonSummary[];
  episodes?: EpisodeRow[];
  history: ActivityEntry[];
}

export interface HoldBody {
  hold: boolean;
}

export interface HoldResult {
  automation_hold: boolean;
}

// ---------- Actions ----------

export type ActionName =
  | "unmonitor"
  | "delete"
  | "exclude"
  | "tag"
  | "ignore"
  | "ignore_server"
  | "mark_reviewed"
  | "unignore"
  | "mark_new";

export type DeleteMode = "files" | "remove_item";
export type ActionScope = "item" | "episodes";

export interface ActionRequest {
  finding_id: number;
  action: ActionName;
  scope?: ActionScope;
  episode_ids?: number[];
  delete_mode?: DeleteMode;
  add_exclusion?: boolean;
  server_id?: string | null;
}

/** POST /actions (returns ActivityEntry[]) and POST /actions/preview. Deletes need confirm. */
export interface ActionsBody {
  requests: ActionRequest[];
  confirm?: boolean;
}

export interface ActionPreview {
  items: number;
  episodes: number;
  bytes: number;
}

// ---------- Activity ----------

export type Actor = "ui" | "auto";
export type ActionResult = "ok" | "error" | "dry_run";

export interface ActivityEntry {
  id: number;
  ts: ISODateTime;
  actor: Actor;
  rule_id: string | null;
  rule_name: string | null;
  scan_run_id: number | null;
  instance_id: string;
  instance_name: string;
  finding_id: number | null;
  arr_id: number;
  item_title: string;
  /** An ActionName, or a system action such as "clear_hold" or a loss-handling step. */
  action: string;
  scope: Record<string, unknown>;
  prior_state: Record<string, unknown>;
  result: ActionResult;
  message: string | null;
  undoable: boolean;
  undone_at: ISODateTime | null;
  undo_of_id: number | null;
  can_undo: boolean;
}

export interface ActivityQuery {
  actor?: Actor;
  action?: string;
  instance?: string;
  rule?: string;
  since?: ISODateTime;
  until?: ISODateTime;
  page?: number;
  page_size?: number;
}

// ---------- Dashboard ----------

export interface DashboardServer {
  id: string;
  label: string;
  reachability: Reachability;
  last_indexed_at: ISODateTime | null;
}

export interface DashboardInstance {
  id: string;
  name: string;
  /** Health from the newest scan that read instances; null when never read. */
  ok: boolean | null;
}

export interface Dashboard {
  counts: {
    by_type: Record<FindingType, number>;
    by_coverage: Record<Coverage, number>;
    by_state: Record<ReviewState, number>;
  };
  reclaimable_bytes: number;
  lost: number;
  last_scan: ScanRun | null;
  next_scan: ISODateTime | null;
  servers: DashboardServer[];
  instances: DashboardInstance[];
  config_error: string | null;
}

// ---------- Automation rules ----------

export type RuleAction = "unmonitor" | "delete" | "exclude" | "tag" | "ignore";

export interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  action: RuleAction;
  delete_mode: DeleteMode;
  add_exclusion: boolean;
  delete_confirmed: boolean;
  finding_types: FindingType[];
  instances: string[];
  servers: string[];
  min_servers: number;
  coverage: Coverage;
  quality: Quality[];
  edition: EditionCmp[];
  match_methods: MatchMethod[];
  wait_scans: number;
  dry_run_next: boolean;
}

/** GET /rules items (POST/PUT/PATCH/dry-run and reorder return plain Rule). */
export interface RuleWithRun extends Rule {
  last_run: ISODateTime | null;
  last_affected: number;
}

/** POST /rules, PUT/PATCH /rules/{id}. id/last_run/last_affected are ignored if sent. */
export type RuleIn = Partial<Omit<Rule, "id">>;

/** GET /rules/warning?action= */
export interface RuleWarning {
  text: string;
}

export interface ReorderIn {
  ids: string[];
}

export interface PreviewIn {
  rule: RuleIn & { id?: string };
}

export interface RulePreviewItem {
  finding_id: number;
  title: string | null;
  episodes: number;
}

export interface RulePreview {
  count: number;
  items: RulePreviewItem[];
}

// ---------- Notifications ----------

export type NotificationType = "email" | "discord" | "webhook" | "apprise";

export interface NotifyEvents {
  new_findings: boolean;
  new_findings_types: FindingType[];
  new_findings_instances: string[];
  automation_actions: boolean;
  automation_deletes: boolean;
  finding_lost: boolean;
  scan_problems: boolean;
  app_errors: boolean;
}

export interface DigestSchedule {
  mode: "off" | "daily" | "weekly" | "cron";
  /** HH:MM */
  time: string;
  /** 0 = Monday ... 6 = Sunday */
  weekday: number;
  cron: string | null;
}

interface ConnBase {
  id: string;
  name: string;
  enabled: boolean;
  events: NotifyEvents;
  digest: DigestSchedule;
}

/** Secret fields (password, webhook_url, url, urls) come back masked. */
export interface EmailConn extends ConnBase {
  type: "email";
  host: string;
  port: number;
  tls: "starttls" | "ssl" | "none";
  username: string | null;
  password: string | null;
  from_addr: string;
  to: string[];
}

export interface DiscordConn extends ConnBase {
  type: "discord";
  webhook_url: string;
}

export interface WebhookConn extends ConnBase {
  type: "webhook";
  url: string;
  method: "POST" | "PUT";
  headers: Record<string, string>;
}

export interface AppriseConn extends ConnBase {
  type: "apprise";
  urls: string[];
}

export type NotificationConnection = EmailConn | DiscordConn | WebhookConn | AppriseConn;

/** GET /notifications items (POST/PUT/PATCH return the connection without last_sent). */
export type NotificationConnectionWithLast = NotificationConnection & {
  last_sent: ISODateTime | null;
};

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/** POST/PUT /notifications. Masked secrets sent back unchanged keep the stored value. */
export type NotificationConnectionIn = DistributiveOmit<NotificationConnection, "id">;

/** PATCH /notifications/{id}: changed fields only (a type change must include its fields). */
export type NotificationConnectionPatch = Partial<DistributiveOmit<NotificationConnection, "id">>;

/** POST /notifications/{id}/test */
export type NotificationTestResult = { ok: true } | { ok: false; error: string };

/** GET /notifications/history?limit= */
export interface NotificationHistoryEntry {
  ts: ISODateTime;
  connection_id: string;
  connection_name: string | null;
  event: string;
  success: boolean;
  error: string | null;
}

// ---------- Logs ----------

export type LogQueryLevel = LogLevel | "CRITICAL";

/** GET /logs?level=&limit= (newest first). /logs/download returns a zip. */
export interface LogEntry {
  ts: string | null;
  level: string | null;
  logger: string | null;
  msg: string | null;
  exc: string | null;
}

// ---------- Health ----------

export interface Health {
  status: string;
}
