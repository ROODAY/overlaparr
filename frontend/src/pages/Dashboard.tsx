import { useQuery } from "@tanstack/react-query";
import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type {
  Dashboard as DashboardData,
  DashboardInstance,
  DashboardServer,
  ScanRun,
} from "../api/types";
import { Badge, type BadgeVariant } from "../components/Badge";
import { CheckNowButton } from "../features/scan/CheckNowButton";
import type { ScanEventsOptions } from "../features/scan/useScanEvents";
import { formatBytes, formatDateTime, formatRelative } from "../lib/format";

const POLL_MS = 30_000;

export function Dashboard({ scanOptions }: { scanOptions?: ScanEventsOptions }) {
  // Same query (and interval) as the Layout's config-error banner.
  const dashboard = useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<DashboardData>("/dashboard"),
    refetchInterval: POLL_MS,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-slate-100">Dashboard</h1>
        <CheckNowButton scanOptions={scanOptions} />
      </div>
      {dashboard.isPending ? (
        <p className="text-slate-400">Loading…</p>
      ) : dashboard.isError ? (
        <p role="alert" className="text-red-300">
          {dashboard.error instanceof ApiError
            ? dashboard.error.detail
            : "Could not load the dashboard"}
        </p>
      ) : (
        <Cards data={dashboard.data} />
      )}
    </div>
  );
}

function Cards({ data }: { data: DashboardData }) {
  const { by_type, by_coverage, by_state } = data.counts;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card title="Findings" className="md:col-span-2 xl:col-span-3">
        <div className="grid gap-4 sm:grid-cols-3">
          <Counts
            heading="Type"
            rows={[
              ["Wanted elsewhere", by_type.wanted_elsewhere],
              ["On disk", by_type.on_disk],
            ]}
          />
          <Counts
            heading="Coverage"
            rows={[
              ["Full", by_coverage.full],
              ["Partial", by_coverage.partial],
            ]}
          />
          <Counts
            heading="State"
            rows={[
              ["New", by_state.new],
              ["Reviewed", by_state.reviewed],
              ["Ignored", by_state.ignored],
              ["Actioned", by_state.actioned],
            ]}
          />
        </div>
        <p className="mt-4 text-sm">
          {data.lost > 0 ? (
            <Link to="/findings?lifecycle=lost" className="text-accent hover:underline">
              {data.lost} lost {data.lost === 1 ? "finding" : "findings"}
            </Link>
          ) : (
            <span className="text-slate-400">No lost findings</span>
          )}
        </p>
      </Card>

      <Card title="Reclaimable space">
        <p className="text-3xl font-semibold text-slate-100">
          {formatBytes(data.reclaimable_bytes)}
        </p>
        <p className="mt-1 text-sm text-slate-400">On disk and fully available elsewhere</p>
      </Card>

      <Card title="Scans">
        <LastScan run={data.last_scan} />
        {data.next_scan && (
          <p className="mt-3 text-sm text-slate-300">
            Next scan{" "}
            <time dateTime={data.next_scan} title={formatDateTime(data.next_scan)}>
              {formatRelative(data.next_scan)}
            </time>
          </p>
        )}
      </Card>

      <Card title="Instances">
        {data.instances.length === 0 ? (
          <p className="text-sm text-slate-400">No instances configured</p>
        ) : (
          <ul className="space-y-2">
            {data.instances.map((i) => (
              <InstanceRow key={i.id} instance={i} />
            ))}
          </ul>
        )}
      </Card>

      <Card title="Servers" className="md:col-span-2 xl:col-span-3">
        {data.servers.length === 0 ? (
          <p className="text-sm text-slate-400">No other servers found yet</p>
        ) : (
          <>
            <ul className="divide-y divide-slate-800">
              {data.servers.map((s) => (
                <ServerRow key={s.id} server={s} />
              ))}
            </ul>
            <p className="mt-3 text-xs text-slate-500">
              Offline and stale servers keep their last known library; their findings are not
              treated as removed.
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

function Card({
  title,
  className = "",
  children,
}: {
  title: string;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className={`rounded-lg border border-slate-800 bg-slate-950/50 p-4 ${className}`}
    >
      <h2 id={id} className="mb-3 text-sm font-semibold tracking-wide text-slate-400 uppercase">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Counts({ heading, rows }: { heading: string; rows: [string, number][] }) {
  return (
    <div>
      <h3 className="mb-1 text-xs text-slate-500">{heading}</h3>
      <dl className="space-y-1">
        {rows.map(([label, n]) => (
          <div key={label} className="flex justify-between gap-2 text-sm">
            <dt className="text-slate-300">{label}</dt>
            <dd className="font-medium text-slate-100 tabular-nums">{n}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const RUN_BADGE: Record<ScanRun["status"], BadgeVariant> = {
  ok: "good",
  failed: "bad",
  running: "neutral",
};

function When({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} title={formatDateTime(iso)}>
      {formatRelative(iso)}
    </time>
  );
}

function LastScan({ run }: { run: ScanRun | null }) {
  if (!run) return <p className="text-sm text-slate-400">No scan yet</p>;
  return (
    <div className="space-y-1 text-sm text-slate-300">
      <p className="flex flex-wrap items-center gap-2">
        Last scan <Badge variant={RUN_BADGE[run.status]}>{run.status}</Badge>
        <span className="text-slate-500">({run.trigger})</span>
      </p>
      <p className="text-slate-400">
        {run.finished_at ? (
          <>
            Finished <When iso={run.finished_at} />
          </>
        ) : (
          <>
            Started <When iso={run.started_at} />
          </>
        )}
      </p>
      {run.error && <p className="break-words text-red-300">{run.error}</p>}
    </div>
  );
}

function InstanceRow({ instance }: { instance: DashboardInstance }) {
  const [variant, label]: [BadgeVariant, string] =
    instance.ok === null
      ? ["neutral", "not checked yet"]
      : instance.ok
        ? ["good", "ok"]
        : ["bad", "failed"];
  return (
    <li className="flex items-center justify-between gap-2 text-sm">
      <span className="text-slate-200">{instance.name}</span>
      <Badge variant={variant}>{label}</Badge>
    </li>
  );
}

const REACHABILITY: Record<DashboardServer["reachability"], BadgeVariant> = {
  online: "good",
  degraded: "caution",
  partial: "caution",
  stale: "caution",
  offline: "bad",
};

function ServerRow({ server }: { server: DashboardServer }) {
  const unreachable = server.reachability === "offline" || server.reachability === "stale";
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
      <span className="text-slate-200">{server.label}</span>
      <span className="flex items-center gap-2 text-slate-400">
        <Badge variant={REACHABILITY[server.reachability]}>{server.reachability}</Badge>
        {server.last_indexed_at ? (
          <span>
            {unreachable ? `${server.reachability} — last seen ` : "indexed "}
            <When iso={server.last_indexed_at} />
          </span>
        ) : (
          <span>never indexed</span>
        )}
      </span>
    </li>
  );
}
