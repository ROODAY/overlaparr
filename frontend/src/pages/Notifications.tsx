import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type {
  DigestSchedule,
  FindingType,
  Instance,
  NotificationConnection,
  NotificationConnectionIn,
  NotificationConnectionWithLast,
  NotificationHistoryEntry,
  NotificationTestResult,
  NotificationType,
  NotifyEvents,
} from "../api/types";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SecretInput } from "../components/SecretInput";
import { Switch } from "../components/Switch";
import { useToast } from "../components/useToast";
import { formatDateTime, formatRelative } from "../lib/format";

const NOTIFICATIONS = ["notifications"];
const HISTORY_LIMIT = 50;

const SMALL_BUTTON = "rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:opacity-50";
const INPUT =
  "mt-1 w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-slate-100 focus:border-accent focus:outline-none";

const TYPE_LABEL: Record<NotificationType, string> = {
  email: "Email",
  discord: "Discord",
  webhook: "Webhook",
  apprise: "Apprise",
};

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type EventFlag =
  | "new_findings"
  | "automation_actions"
  | "automation_deletes"
  | "finding_lost"
  | "scan_problems"
  | "app_errors";

/** Event flags in display order: [flag, form label, short list summary]. */
const EVENT_FLAGS: [EventFlag, string, string][] = [
  ["new_findings", "New findings after a scan", "New findings"],
  ["automation_actions", "Automation took action", "Automation actions"],
  ["automation_deletes", "Automation deleted files", "Automation deletes"],
  ["finding_lost", "Finding lost", "Finding lost"],
  ["scan_problems", "Scan or server problems", "Scan problems"],
  ["app_errors", "Application errors", "App errors"],
];

const FINDING_TYPES: [FindingType, string][] = [
  ["wanted_elsewhere", "Wanted elsewhere"],
  ["on_disk", "On disk"],
];

const NO_EVENTS: NotifyEvents = {
  new_findings: false,
  new_findings_types: [],
  new_findings_instances: [],
  automation_actions: false,
  automation_deletes: false,
  finding_lost: false,
  scan_problems: false,
  app_errors: false,
};

const DEFAULT_DIGEST: DigestSchedule = { mode: "off", time: "09:00", weekday: 0, cron: null };

function message(error: unknown): string {
  return error instanceof ApiError ? error.detail : "could not reach Overlaparr";
}

function triggersSummary(events: NotifyEvents): string {
  const on = EVENT_FLAGS.filter(([flag]) => events[flag]).map(([, , short]) => short);
  return on.length ? on.join(", ") : "None";
}

function digestSummary(digest: DigestSchedule): string {
  switch (digest.mode) {
    case "daily":
      return `Daily at ${digest.time}`;
    case "weekly":
      return `Weekly on ${WEEKDAYS[digest.weekday] ?? digest.weekday} at ${digest.time}`;
    case "cron":
      return "Cron";
    default:
      return "Off";
  }
}

type Tab = "connections" | "history";

export function Notifications() {
  const [tab, setTab] = useState<Tab>("connections");
  const tabClass = (t: Tab) =>
    `rounded px-3 py-1.5 text-sm ${
      tab === t ? "bg-slate-700 text-slate-100" : "text-slate-400 hover:bg-slate-800"
    }`;
  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-100">Notifications</h1>
      <p className="mt-1 text-sm text-slate-400">
        Where Overlaparr sends alerts and digests, and what it has sent.
      </p>
      <div className="mt-4 flex gap-1">
        <button
          type="button"
          aria-pressed={tab === "connections"}
          onClick={() => setTab("connections")}
          className={tabClass("connections")}
        >
          Connections
        </button>
        <button
          type="button"
          aria-pressed={tab === "history"}
          onClick={() => setTab("history")}
          className={tabClass("history")}
        >
          History
        </button>
      </div>
      {tab === "connections" ? <Connections /> : <History />}
    </div>
  );
}

function LoadError({ error, fallback, onRetry }: { error: unknown; fallback: string; onRetry: () => void }) {
  return (
    <div className="mt-6">
      <p role="alert" className="text-red-300">
        {error instanceof ApiError ? error.detail : fallback}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 rounded border border-slate-600 px-3 py-1 text-sm text-slate-200 hover:bg-slate-700"
      >
        Retry
      </button>
    </div>
  );
}

function Connections() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const connections = useQuery({
    queryKey: NOTIFICATIONS,
    queryFn: () => api.get<NotificationConnectionWithLast[]>("/notifications"),
  });
  // "new" opens the form empty; a connection opens it for editing.
  const [editing, setEditing] = useState<NotificationConnectionWithLast | "new" | null>(null);
  const [deleting, setDeleting] = useState<NotificationConnectionWithLast | null>(null);

  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.patch<NotificationConnection>(`/notifications/${id}`, { enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS }),
    onError: (e) => toast(`Could not update the connection: ${message(e)}`, "error"),
  });

  const test = useMutation({
    mutationFn: (id: string) => api.post<NotificationTestResult>(`/notifications/${id}/test`),
    onSuccess: (result) => {
      if (result.ok) toast("Test message sent");
      else toast(result.error, "error");
    },
    onError: (e) => toast(message(e), "error"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/notifications/${id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS });
      toast("Connection deleted");
      setDeleting(null);
    },
    onError: (e) => {
      toast(`Could not delete the connection: ${message(e)}`, "error");
      setDeleting(null);
    },
  });

  return (
    <div className="mt-4">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover"
        >
          Add connection
        </button>
      </div>

      {connections.isPending ? (
        <p className="mt-6 text-slate-400">Loading…</p>
      ) : connections.isError ? (
        <LoadError
          error={connections.error}
          fallback="Could not load connections"
          onRetry={() => void connections.refetch()}
        />
      ) : connections.data.length === 0 ? (
        <p className="mt-4 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
          No connections yet. Add one to get alerts by email, Discord, webhook or Apprise.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2">Connection</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2">Enabled</th>
                <th className="px-3 py-2">Triggers</th>
                <th className="px-3 py-2">Digest</th>
                <th className="px-3 py-2">Last sent</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/60">
              {connections.data.map((conn) => (
                <tr key={conn.id} className="align-middle">
                  <td className="px-3 py-2 text-slate-100">{conn.name}</td>
                  <td className="px-3 py-2">
                    <Badge>{TYPE_LABEL[conn.type]}</Badge>
                  </td>
                  <td className="px-3 py-2">
                    <Switch
                      label={`Enable ${conn.name}`}
                      checked={conn.enabled}
                      disabled={setEnabled.isPending}
                      onChange={(enabled) => setEnabled.mutate({ id: conn.id, enabled })}
                    />
                  </td>
                  <td className="px-3 py-2 text-slate-300">{triggersSummary(conn.events)}</td>
                  <td className="px-3 py-2 text-slate-300">
                    <span title={conn.digest.mode === "cron" ? (conn.digest.cron ?? "") : undefined}>
                      {digestSummary(conn.digest)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {conn.last_sent ? (
                      <span title={formatDateTime(conn.last_sent)}>
                        {formatRelative(conn.last_sent)}
                      </span>
                    ) : (
                      "Never"
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        disabled={test.isPending}
                        onClick={() => test.mutate(conn.id)}
                        className={SMALL_BUTTON}
                      >
                        Test
                      </button>
                      <button type="button" onClick={() => setEditing(conn)} className={SMALL_BUTTON}>
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleting(conn)}
                        className={SMALL_BUTTON}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <Modal
          title={editing === "new" ? "Add connection" : "Edit connection"}
          onClose={() => setEditing(null)}
        >
          <ConnectionForm
            key={editing === "new" ? "new" : editing.id}
            initial={editing === "new" ? undefined : editing}
            onSaved={() => setEditing(null)}
            onCancel={() => setEditing(null)}
          />
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          open
          title={`Delete ${deleting.name}?`}
          body={<>This removes the connection from config.yaml. Overlaparr stops sending to it.</>}
          confirmLabel="Delete connection"
          destructive
          confirmDisabled={remove.isPending}
          onConfirm={() => remove.mutate(deleting.id)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

function History() {
  const history = useQuery({
    queryKey: [...NOTIFICATIONS, "history"],
    queryFn: () =>
      api.get<NotificationHistoryEntry[]>(`/notifications/history?limit=${HISTORY_LIMIT}`),
  });
  if (history.isPending) return <p className="mt-6 text-slate-400">Loading…</p>;
  if (history.isError) {
    return (
      <LoadError
        error={history.error}
        fallback="Could not load notification history"
        onRetry={() => void history.refetch()}
      />
    );
  }
  if (history.data.length === 0) {
    return (
      <p className="mt-4 rounded-lg border border-slate-700 bg-slate-800 p-4 text-slate-300">
        Nothing sent yet
      </p>
    );
  }
  return (
    <div className="mt-4 overflow-x-auto rounded-lg border border-slate-700">
      <table className="w-full text-sm">
        <thead className="bg-slate-800 text-left text-xs uppercase tracking-wide text-slate-400">
          <tr>
            <th className="px-3 py-2">Time</th>
            <th className="px-3 py-2">Connection</th>
            <th className="px-3 py-2">Event</th>
            <th className="px-3 py-2">Result</th>
            <th className="px-3 py-2">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-700/60">
          {history.data.map((entry, i) => (
            // History rows have no id; position in this newest-first snapshot is the key.
            <tr key={i} className="align-middle">
              <td className="whitespace-nowrap px-3 py-2 text-slate-300">
                {formatDateTime(entry.ts)}
              </td>
              <td className="px-3 py-2 text-slate-100">
                {entry.connection_name ?? entry.connection_id}
              </td>
              <td className="px-3 py-2 text-slate-300">{entry.event}</td>
              <td className="px-3 py-2">
                {entry.success ? <Badge variant="good">Sent</Badge> : <Badge variant="bad">Failed</Badge>}
              </td>
              <td className="px-3 py-2 text-red-300">{entry.error}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Parses "Name: value" lines; blank lines are skipped. Returns an error for a line with no name. */
function parseHeaders(text: string): { headers: Record<string, string> } | { error: string } {
  const headers: Record<string, string> = {};
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const colon = line.indexOf(":");
    const name = colon > 0 ? line.slice(0, colon).trim() : "";
    if (!name) return { error: `Header line ${i + 1} must look like "Name: value"` };
    headers[name] = line.slice(colon + 1).trim();
  }
  return { headers };
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

function ConnectionForm({
  initial,
  onSaved,
  onCancel,
}: {
  /** Existing connection to edit (secrets come back masked); omit to add a new one. */
  initial?: NotificationConnectionWithLast;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const id = useId();
  const field = (name: string) => `${id}-${name}`;
  const email = initial?.type === "email" ? initial : undefined;
  const discord = initial?.type === "discord" ? initial : undefined;
  const webhook = initial?.type === "webhook" ? initial : undefined;
  const apprise = initial?.type === "apprise" ? initial : undefined;

  const [name, setName] = useState(initial?.name ?? "");
  const [type, setType] = useState<NotificationType>(initial?.type ?? "email");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [host, setHost] = useState(email?.host ?? "");
  const [port, setPort] = useState(String(email?.port ?? 587));
  const [tls, setTls] = useState(email?.tls ?? "starttls");
  const [username, setUsername] = useState(email?.username ?? "");
  const [password, setPassword] = useState(email?.password ?? "");
  const [fromAddr, setFromAddr] = useState(email?.from_addr ?? "");
  const [to, setTo] = useState(email?.to.join(", ") ?? "");
  const [webhookUrl, setWebhookUrl] = useState(discord?.webhook_url ?? "");
  const [url, setUrl] = useState(webhook?.url ?? "");
  const [method, setMethod] = useState(webhook?.method ?? "POST");
  const [headers, setHeaders] = useState(
    Object.entries(webhook?.headers ?? {})
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n"),
  );
  const [urls, setUrls] = useState(apprise?.urls.join("\n") ?? "");
  const [events, setEvents] = useState<NotifyEvents>(initial?.events ?? NO_EVENTS);
  const [digest, setDigest] = useState<DigestSchedule>(initial?.digest ?? DEFAULT_DIGEST);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: NotificationConnectionIn) =>
      initial
        ? api.put<NotificationConnection>(`/notifications/${initial.id}`, body)
        : api.post<NotificationConnection>("/notifications", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS });
      toast("Connection saved");
      onSaved();
    },
    onError: (e) => setError(message(e)),
  });

  function build(): NotificationConnectionIn | string {
    const base = { name: name.trim(), enabled, events, digest };
    switch (type) {
      case "email":
        return {
          ...base,
          type,
          host: host.trim(),
          port: Number(port),
          tls,
          username: username.trim() || null,
          password: password || null,
          from_addr: fromAddr.trim(),
          to: to
            .split(",")
            .map((a) => a.trim())
            .filter(Boolean),
        };
      case "discord":
        return { ...base, type, webhook_url: webhookUrl.trim() };
      case "webhook": {
        const parsed = parseHeaders(headers);
        if ("error" in parsed) return parsed.error;
        return { ...base, type, url: url.trim(), method, headers: parsed.headers };
      }
      case "apprise":
        return { ...base, type, urls: lines(urls) };
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const body = build();
    if (typeof body === "string") {
      setError(body);
      return;
    }
    setError(null);
    save.mutate(body);
  }

  const setFlag = (flag: EventFlag, on: boolean) => setEvents((ev) => ({ ...ev, [flag]: on }));

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id={field("name")} label="Name">
          <input
            id={field("name")}
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={INPUT}
          />
        </Field>
        <Field id={field("type")} label="Type">
          <select
            id={field("type")}
            value={type}
            onChange={(e) => setType(e.target.value as NotificationType)}
            className={INPUT}
          >
            {(Object.keys(TYPE_LABEL) as NotificationType[]).map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="flex items-center gap-2 text-sm text-slate-300">
        <Switch label="Enabled" checked={enabled} onChange={setEnabled} />
        <span aria-hidden="true">Enabled</span>
      </div>

      {type === "email" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id={field("host")} label="Host">
            <input
              id={field("host")}
              required
              value={host}
              onChange={(e) => setHost(e.target.value)}
              className={INPUT}
            />
          </Field>
          <Field id={field("port")} label="Port">
            <input
              id={field("port")}
              type="number"
              min={1}
              max={65535}
              required
              value={port}
              onChange={(e) => setPort(e.target.value)}
              className={INPUT}
            />
          </Field>
          <Field id={field("tls")} label="TLS">
            <select
              id={field("tls")}
              value={tls}
              onChange={(e) => setTls(e.target.value as typeof tls)}
              className={INPUT}
            >
              <option value="starttls">StartTLS</option>
              <option value="ssl">SSL</option>
              <option value="none">None</option>
            </select>
          </Field>
          <Field id={field("username")} label="Username">
            <input
              id={field("username")}
              autoComplete="off"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={INPUT}
            />
          </Field>
          <Field id={field("password")} label="Password">
            <SecretInput
              id={field("password")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT}
            />
          </Field>
          <Field id={field("from")} label="From address">
            <input
              id={field("from")}
              required
              value={fromAddr}
              onChange={(e) => setFromAddr(e.target.value)}
              className={INPUT}
            />
          </Field>
          <div className="sm:col-span-2">
            <Field id={field("to")} label="To" hint="Comma-separated addresses">
              <input
                id={field("to")}
                required
                value={to}
                onChange={(e) => setTo(e.target.value)}
                className={INPUT}
              />
            </Field>
          </div>
        </div>
      )}

      {type === "discord" && (
        <Field id={field("webhook-url")} label="Webhook URL">
          <SecretInput
            id={field("webhook-url")}
            required
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            className={INPUT}
          />
        </Field>
      )}

      {type === "webhook" && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <Field id={field("url")} label="URL">
              <SecretInput
                id={field("url")}
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className={INPUT}
              />
            </Field>
            <Field id={field("method")} label="Method">
              <select
                id={field("method")}
                value={method}
                onChange={(e) => setMethod(e.target.value as typeof method)}
                className={INPUT}
              >
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
              </select>
            </Field>
          </div>
          <Field id={field("headers")} label="Headers" hint="One Name: value header per line">
            <textarea
              id={field("headers")}
              rows={3}
              value={headers}
              onChange={(e) => setHeaders(e.target.value)}
              className={`${INPUT} font-mono text-sm`}
            />
          </Field>
        </div>
      )}

      {type === "apprise" && (
        <Field id={field("urls")} label="URLs" hint="One Apprise URL per line">
          <textarea
            id={field("urls")}
            rows={3}
            required
            value={urls}
            onChange={(e) => setUrls(e.target.value)}
            className={`${INPUT} font-mono text-sm`}
          />
        </Field>
      )}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-slate-200">Send when</legend>
        {EVENT_FLAGS.map(([flag, label]) => (
          <div key={flag}>
            <Checkbox label={label} checked={events[flag]} onChange={(on) => setFlag(flag, on)} />
            {flag === "new_findings" && events.new_findings && (
              <NewFindingsOptions events={events} onChange={setEvents} />
            )}
          </div>
        ))}
      </fieldset>

      <DigestFields id={id} digest={digest} onChange={setDigest} />

      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-4 py-2 text-sm text-slate-300 hover:bg-slate-700"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={save.isPending}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </form>
  );
}

function NewFindingsOptions({
  events,
  onChange,
}: {
  events: NotifyEvents;
  onChange: (update: (ev: NotifyEvents) => NotifyEvents) => void;
}) {
  const instances = useQuery({
    queryKey: queryKeys.instances,
    queryFn: () => api.get<Instance[]>("/instances"),
  });
  const toggle = <T,>(list: T[], value: T, on: boolean) =>
    on ? [...list.filter((v) => v !== value), value] : list.filter((v) => v !== value);
  return (
    <div className="mt-2 ml-6 grid gap-3 sm:grid-cols-2">
      <fieldset>
        <legend className="text-xs text-slate-400">Finding types</legend>
        {FINDING_TYPES.map(([value, label]) => (
          <Checkbox
            key={value}
            label={label}
            checked={events.new_findings_types.includes(value)}
            onChange={(on) =>
              onChange((ev) => ({
                ...ev,
                new_findings_types: toggle(ev.new_findings_types, value, on),
              }))
            }
          />
        ))}
      </fieldset>
      <fieldset>
        <legend className="text-xs text-slate-400">Instances (none ticked means all)</legend>
        {instances.isPending && <p className="text-xs text-slate-500">Loading…</p>}
        {instances.isError && <p className="text-xs text-red-300">Could not load instances</p>}
        {instances.data?.map((instance) => (
          <Checkbox
            key={instance.id}
            label={instance.name}
            checked={events.new_findings_instances.includes(instance.id)}
            onChange={(on) =>
              onChange((ev) => ({
                ...ev,
                new_findings_instances: toggle(ev.new_findings_instances, instance.id, on),
              }))
            }
          />
        ))}
      </fieldset>
    </div>
  );
}

function DigestFields({
  id,
  digest,
  onChange,
}: {
  id: string;
  digest: DigestSchedule;
  onChange: (digest: DigestSchedule) => void;
}) {
  const ids = { mode: `${id}-digest`, time: `${id}-time`, weekday: `${id}-weekday`, cron: `${id}-cron` };
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Field id={ids.mode} label="Digest">
        <select
          id={ids.mode}
          value={digest.mode}
          onChange={(e) => onChange({ ...digest, mode: e.target.value as DigestSchedule["mode"] })}
          className={INPUT}
        >
          <option value="off">Off</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="cron">Custom cron</option>
        </select>
      </Field>
      {digest.mode === "weekly" && (
        <Field id={ids.weekday} label="Weekday">
          <select
            id={ids.weekday}
            value={digest.weekday}
            onChange={(e) => onChange({ ...digest, weekday: Number(e.target.value) })}
            className={INPUT}
          >
            {WEEKDAYS.map((day, i) => (
              <option key={day} value={i}>
                {day}
              </option>
            ))}
          </select>
        </Field>
      )}
      {(digest.mode === "daily" || digest.mode === "weekly") && (
        <Field id={ids.time} label="Time">
          <input
            id={ids.time}
            type="time"
            required
            value={digest.time}
            onChange={(e) => onChange({ ...digest, time: e.target.value })}
            className={INPUT}
          />
        </Field>
      )}
      {digest.mode === "cron" && (
        <div className="sm:col-span-2">
          <Field id={ids.cron} label="Cron expression">
            <input
              id={ids.cron}
              required
              placeholder="0 9 * * 1-5"
              value={digest.cron ?? ""}
              onChange={(e) => onChange({ ...digest, cron: e.target.value.trim() || null })}
              className={`${INPUT} font-mono`}
            />
          </Field>
        </div>
      )}
    </div>
  );
}

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm text-slate-300">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
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
    <label className="flex items-center gap-2 py-0.5 text-sm text-slate-300">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/60 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="mt-16 w-full max-w-2xl rounded-lg border border-slate-700 bg-slate-800 p-5 shadow-xl"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold text-slate-100">
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded px-2 text-xl text-slate-400 hover:bg-slate-700 hover:text-slate-100"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
