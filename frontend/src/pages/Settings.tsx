import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type {
  CronPreview,
  LogLevel,
  Settings as SettingsData,
  SettingsUpdate,
} from "../api/types";
import { useToast } from "../components/useToast";
import { formatDateTime } from "../lib/format";

const SETTINGS_KEY = ["settings"] as const;
const PREVIEW_DEBOUNCE_MS = 300;

const CRON_PRESETS = [
  { value: "0 * * * *", label: "Every hour" },
  { value: "0 */3 * * *", label: "Every 3 hours" },
  { value: "0 */6 * * *", label: "Every 6 hours" },
  { value: "0 */12 * * *", label: "Every 12 hours" },
  { value: "0 0 * * *", label: "Every day" },
] as const;

const CUSTOM_PRESET = "custom";

const LOG_LEVELS: LogLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR"];

function presetFor(cron: string): string {
  return CRON_PRESETS.find((p) => p.value === cron)?.value ?? CUSTOM_PRESET;
}

function detail(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback;
}

/** A value that only follows `value` once it has stopped changing for `delay` ms. */
function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

const INPUT =
  "mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none";
const LABEL = "block text-sm font-medium text-slate-300";
const HELP = "mt-1 text-xs text-slate-500";

export function Settings() {
  const settings = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => api.get<SettingsData>("/settings"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-100">Settings</h1>
        <p className="mt-1 text-sm text-slate-400">
          Changes are written to config.yaml and applied immediately.
        </p>
      </div>
      {settings.isPending ? (
        <p className="text-slate-400">Loading…</p>
      ) : settings.isError ? (
        <div>
          <p role="alert" className="text-red-300">
            {detail(settings.error, "Could not load settings")}
          </p>
          <button
            type="button"
            onClick={() => void settings.refetch()}
            className="mt-3 rounded border border-slate-600 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      ) : (
        <SettingsForm settings={settings.data} />
      )}
    </div>
  );
}

interface FormState {
  /** The preset expression, or CUSTOM_PRESET when the user is editing a free-form cron. */
  preset: string;
  cron: string;
  serverTimeout: string;
  staleHours: string;
  tagsEnabled: boolean;
  dupe: string;
  partial: string;
  ignore: string;
  perServerTags: boolean;
  localBypass: boolean;
  level: LogLevel;
  maxBytes: string;
  backupCount: string;
  retentionDays: string;
  appUrl: string;
}

function formFromSettings(s: SettingsData): FormState {
  return {
    preset: presetFor(s.scan.cron),
    cron: s.scan.cron,
    serverTimeout: String(s.scan.server_timeout_seconds),
    staleHours: String(s.scan.stale_after_hours),
    tagsEnabled: s.tags.enabled,
    dupe: s.tags.dupe,
    partial: s.tags.partial,
    ignore: s.tags.ignore,
    perServerTags: s.tags.per_server_tags,
    localBypass: s.auth.local_bypass,
    level: s.logging.level,
    maxBytes: String(s.logging.max_bytes),
    backupCount: String(s.logging.backup_count),
    retentionDays: s.action_log.retention_days === null ? "" : String(s.action_log.retention_days),
    appUrl: s.notifications.app_url ?? "",
  };
}

function SettingsForm({ settings }: { settings: SettingsData }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => formFromSettings(settings));

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const debouncedCron = useDebounced(form.cron.trim(), PREVIEW_DEBOUNCE_MS);
  const preview = useQuery({
    queryKey: ["cron-preview", debouncedCron],
    queryFn: () =>
      api.get<CronPreview>(`/settings/cron-preview?cron=${encodeURIComponent(debouncedCron)}`),
    enabled: debouncedCron !== "",
  });

  const save = useMutation({
    mutationFn: (body: SettingsUpdate) => api.put<SettingsData>("/settings", body),
    onSuccess: (updated) => {
      queryClient.setQueryData(SETTINGS_KEY, updated);
      setForm(formFromSettings(updated));
      toast("Settings saved");
    },
    onError: (e) => toast(`Could not save settings: ${detail(e, "could not reach Overlaparr")}`, "error"),
  });

  const onPresetChange = (value: string) => {
    if (value === CUSTOM_PRESET) {
      set("preset", CUSTOM_PRESET);
    } else {
      setForm((f) => ({ ...f, preset: value, cron: value }));
    }
  };

  const onCronChange = (value: string) => {
    setForm((f) => ({ ...f, cron: value, preset: presetFor(value) }));
  };

  const onSave = () => {
    const appUrl = form.appUrl.trim();
    save.mutate({
      scan: {
        cron: form.cron.trim(),
        server_timeout_seconds: Number(form.serverTimeout),
        stale_after_hours: Number(form.staleHours),
      },
      tags: {
        enabled: form.tagsEnabled,
        dupe: form.dupe,
        partial: form.partial,
        ignore: form.ignore,
        per_server_tags: form.perServerTags,
      },
      logging: {
        level: form.level,
        max_bytes: Number(form.maxBytes),
        backup_count: Number(form.backupCount),
      },
      action_log: {
        retention_days: form.retentionDays.trim() === "" ? null : Number(form.retentionDays),
      },
      auth: { local_bypass: form.localBypass },
      notifications: { app_url: appUrl === "" ? null : appUrl },
    });
  };

  return (
    <div className="max-w-3xl space-y-4">
      {settings.config_error && (
        <div
          role="alert"
          className="rounded border border-red-500/50 bg-red-500/10 px-4 py-3 text-sm text-red-200"
        >
          <p className="font-semibold text-red-300">config.yaml is invalid</p>
          <p className="mt-1">
            The file could not be loaded, so Overlaparr is running on the last valid
            configuration. Fix the file before saving changes.
          </p>
          <pre className="mt-2 font-mono text-xs whitespace-pre-wrap">{settings.config_error}</pre>
        </div>
      )}

      <Section title="Scan schedule">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="settings-cron-preset" className={LABEL}>
              Scan schedule
            </label>
            <select
              id="settings-cron-preset"
              value={form.preset}
              onChange={(e) => onPresetChange(e.target.value)}
              className={INPUT}
            >
              {CRON_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
              <option value={CUSTOM_PRESET}>Custom</option>
            </select>
          </div>
          {form.preset === CUSTOM_PRESET && (
            <div>
              <label htmlFor="settings-cron" className={LABEL}>
                Cron expression
              </label>
              <input
                id="settings-cron"
                type="text"
                value={form.cron}
                onChange={(e) => onCronChange(e.target.value)}
                placeholder="0 */6 * * *"
                className={`${INPUT} font-mono`}
              />
            </div>
          )}
        </div>

        <div className="mt-4">
          <h3 className="text-sm font-medium text-slate-300">Next 5 runs</h3>
          <div className="mt-1">
            {form.cron.trim() === "" ? (
              <p className="text-sm text-slate-500">
                Enter a cron expression to see the next runs.
              </p>
            ) : preview.isPending ? (
              <p className="text-sm text-slate-400">…</p>
            ) : preview.isError ? (
              <p role="alert" className="text-sm text-red-300">
                Preview failed: {detail(preview.error, "could not reach Overlaparr")}
              </p>
            ) : (
              <ul className="space-y-0.5 text-sm text-slate-300">
                {preview.data.next.map((iso) => (
                  <li key={iso}>
                    <time dateTime={iso}>{formatDateTime(iso)}</time>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Section>

      <Section title="Timeouts and staleness">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="settings-timeout" className={LABEL}>
              Server timeout (seconds)
            </label>
            <input
              id="settings-timeout"
              type="number"
              min={1}
              value={form.serverTimeout}
              onChange={(e) => set("serverTimeout", e.target.value)}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="settings-stale" className={LABEL}>
              Mark servers stale after (hours)
            </label>
            <input
              id="settings-stale"
              type="number"
              min={1}
              value={form.staleHours}
              onChange={(e) => set("staleHours", e.target.value)}
              className={INPUT}
            />
          </div>
        </div>
      </Section>

      <Section title="Tags">
        <div className="space-y-3">
          <Checkbox
            label="Enabled"
            checked={form.tagsEnabled}
            onChange={(checked) => set("tagsEnabled", checked)}
          />
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <label htmlFor="settings-tag-dupe" className={LABEL}>
                Dupe tag
              </label>
              <input
                id="settings-tag-dupe"
                type="text"
                value={form.dupe}
                onChange={(e) => set("dupe", e.target.value)}
                placeholder="dupe-elsewhere"
                className={INPUT}
              />
            </div>
            <div>
              <label htmlFor="settings-tag-partial" className={LABEL}>
                Partial tag
              </label>
              <input
                id="settings-tag-partial"
                type="text"
                value={form.partial}
                onChange={(e) => set("partial", e.target.value)}
                placeholder="dupe-partial"
                className={INPUT}
              />
            </div>
            <div>
              <label htmlFor="settings-tag-ignore" className={LABEL}>
                Ignore tag
              </label>
              <input
                id="settings-tag-ignore"
                type="text"
                value={form.ignore}
                onChange={(e) => set("ignore", e.target.value)}
                placeholder="dupe-ignore"
                className={INPUT}
              />
            </div>
          </div>
          <div>
            <p className="text-xs text-slate-500">
              Tag names may only contain lowercase letters, digits and hyphens.
            </p>
            <div className="mt-3">
              <Checkbox
                label="Per-server tags"
                checked={form.perServerTags}
                onChange={(checked) => set("perServerTags", checked)}
              />
              <p className={`${HELP} ml-6`}>Per-server tags are named on-&lt;label&gt;.</p>
            </div>
          </div>
        </div>
      </Section>

      <Section title="Auth">
        <Checkbox
          label="Disable login for local addresses"
          checked={form.localBypass}
          onChange={(checked) => set("localBypass", checked)}
        />
        {form.localBypass && (
          <p
            role="alert"
            className="mt-3 rounded border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm text-amber-200"
          >
            <strong>Security warning.</strong> Requests are trusted when their direct peer address
            is local, so behind Docker port mapping or a reverse proxy{" "}
            <strong>every request looks local</strong> — do not enable this there.
          </p>
        )}
      </Section>

      <Section title="Logging">
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <label htmlFor="settings-log-level" className={LABEL}>
              Level
            </label>
            <select
              id="settings-log-level"
              value={form.level}
              onChange={(e) => set("level", e.target.value as LogLevel)}
              className={INPUT}
            >
              {LOG_LEVELS.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="settings-log-bytes" className={LABEL}>
              Max file size (bytes)
            </label>
            <input
              id="settings-log-bytes"
              type="number"
              min={1}
              value={form.maxBytes}
              onChange={(e) => set("maxBytes", e.target.value)}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor="settings-log-backups" className={LABEL}>
              Backups
            </label>
            <input
              id="settings-log-backups"
              type="number"
              min={0}
              value={form.backupCount}
              onChange={(e) => set("backupCount", e.target.value)}
              className={INPUT}
            />
          </div>
        </div>
      </Section>

      <Section title="Action log">
        <div className="max-w-xs">
          <label htmlFor="settings-retention" className={LABEL}>
            Retention (days)
          </label>
          <input
            id="settings-retention"
            type="number"
            min={0}
            value={form.retentionDays}
            onChange={(e) => set("retentionDays", e.target.value)}
            placeholder="Keep forever"
            className={INPUT}
          />
          <p className={HELP}>Leave blank to keep action log entries forever.</p>
        </div>
      </Section>

      <Section title="Application URL">
        <div className="max-w-md">
          <label htmlFor="settings-app-url" className={LABEL}>
            App URL
          </label>
          <input
            id="settings-app-url"
            type="url"
            value={form.appUrl}
            onChange={(e) => set("appUrl", e.target.value)}
            placeholder="https://overlaparr.example.com"
            className={INPUT}
          />
          <p className={HELP}>Used for links in notifications.</p>
        </div>
      </Section>

      <Section title="Config file">
        <a
          href="/api/v1/settings/config.yaml"
          download
          className="text-accent hover:underline"
        >
          Download config.yaml
        </a>
        <p className={HELP}>Secrets live in secrets.yaml, never in this file.</p>
      </Section>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onSave}
          disabled={save.isPending}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          Save settings
        </button>
        {save.isPending && <span className="text-sm text-slate-400">Saving…</span>}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-700 bg-slate-800/50 p-4">
      <h2 className="mb-3 text-sm font-semibold tracking-wide text-slate-400 uppercase">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-300">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-slate-600 bg-slate-900 accent-accent"
      />
      {label}
    </label>
  );
}
