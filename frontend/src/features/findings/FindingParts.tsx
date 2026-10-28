import type { Finding } from "../../api/types";
import { Badge } from "../../components/Badge";
import { formatBytes } from "../../lib/format";

const TYPE_LABELS: Record<Finding["type"], string> = {
  wanted_elsewhere: "Wanted elsewhere",
  on_disk: "On disk",
};

export function TypeLabel({ finding }: { finding: Finding }) {
  return <>{TYPE_LABELS[finding.type]}</>;
}

/** Selection shared by both views. */
export interface SelectionProps {
  selected: ReadonlySet<number>;
  onToggle: (id: number) => void;
  onToggleAll: (ids: number[], checked: boolean) => void;
}

/**
 * The item's poster from the API row, or a neutral placeholder with the title.
 * The browser never calls Plex or the *arr apps directly.
 */
export function Poster({ finding, className }: { finding: Finding; className: string }) {
  if (finding.poster_url) {
    return (
      <img
        src={finding.poster_url}
        alt={`${finding.title} poster`}
        loading="lazy"
        className={`${className} rounded bg-slate-800 object-cover`}
      />
    );
  }
  return (
    <div
      aria-hidden="true"
      className={`${className} flex items-center justify-center overflow-hidden rounded bg-slate-700 p-1 text-center text-[10px] leading-tight text-slate-300`}
    >
      {finding.title}
    </div>
  );
}

export function Yours({ finding }: { finding: Finding }) {
  const { resolution, size } = finding.yours;
  if (finding.type === "wanted_elsewhere") return <span className="text-slate-400">wanted</span>;
  return (
    <span>
      {resolution ?? "?"}
      {size != null && <span className="text-slate-400"> · {formatBytes(size)}</span>}
    </span>
  );
}

/** One chip per other server: label and best resolution. */
export function ElsewhereChips({ finding }: { finding: Finding }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {finding.elsewhere.map((e) => (
        <Badge
          key={e.server_id}
          variant="neutral"
          title={[e.stale && "stale", e.ignored && "ignored"].filter(Boolean).join(", ") || undefined}
        >
          <span className={e.ignored ? "line-through" : undefined}>
            {e.label} {e.resolution ?? "?"}
            {e.hdr && " HDR"}
          </span>
        </Badge>
      ))}
    </span>
  );
}
