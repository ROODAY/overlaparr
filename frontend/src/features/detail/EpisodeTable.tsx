import type { EpisodeCopy, EpisodeRow } from "../../api/types";
import { formatBytes } from "../../lib/format";

export interface ServerColumn {
  id: string;
  label: string;
}

/** One season's episodes: checkbox, number, title, yours, then one column per server. */
export function EpisodeTable({
  episodes,
  servers,
  selected,
  onToggle,
}: {
  episodes: EpisodeRow[];
  servers: ServerColumn[];
  selected: ReadonlySet<number>;
  onToggle: (id: number) => void;
}) {
  const th = "px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-400";
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-700">
      <table aria-label="Episodes" className="w-full text-sm">
        <thead className="bg-slate-800/60">
          <tr>
            <th className={th}>
              <span className="sr-only">Selected</span>
            </th>
            <th className={th}>Ep</th>
            <th className={th}>Title</th>
            <th className={th}>Yours</th>
            {servers.map((s) => (
              <th key={s.id} className={th}>
                {s.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {episodes.map((e) => (
            <tr key={e.episode_arr_id}>
              <td className="px-3 py-1.5">
                <input
                  type="checkbox"
                  aria-label={`Select ${e.title}`}
                  checked={selected.has(e.episode_arr_id)}
                  onChange={() => onToggle(e.episode_arr_id)}
                />
              </td>
              <td className="px-3 py-1.5 text-slate-400">{e.episode}</td>
              <td className="px-3 py-1.5 text-slate-100">{e.title}</td>
              <td className="px-3 py-1.5">
                {e.yours.state === "have" ? (
                  <span className="text-sky-300">
                    {e.yours.resolution ?? "?"}
                    {e.yours.size != null && ` · ${formatBytes(e.yours.size)}`}
                  </span>
                ) : (
                  <span className="text-amber-300">Wanted</span>
                )}
              </td>
              {servers.map((s) => (
                <td key={s.id} className="px-3 py-1.5">
                  <CopyCell copy={e.copies[s.id]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A server's copy: resolution (+HDR); amber outline when matched by number; dim if stale. */
function CopyCell({ copy }: { copy?: EpisodeCopy }) {
  if (!copy) return <span className="text-slate-600">—</span>;
  const byNumber = copy.matched_by === "number";
  const title = [
    copy.stale && "stale: this server was not reachable at the last check",
    byNumber && "matched by season/episode number, not by ID",
  ]
    .filter(Boolean)
    .join("; ");
  return (
    <span
      title={title || undefined}
      className={`rounded px-1.5 py-0.5 text-xs ${
        byNumber ? "ring-1 ring-inset ring-amber-400 text-amber-200" : "text-green-300"
      } bg-green-500/10 ${copy.stale ? "opacity-50" : ""}`}
    >
      {copy.resolution ?? "?"}
      {copy.hdr && " HDR"}
      {byNumber && " · by number"}
    </span>
  );
}
