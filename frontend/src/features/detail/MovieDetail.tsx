import type { EditionCmp, FindingDetail, Quality } from "../../api/types";
import { Badge, type BadgeVariant } from "../../components/Badge";
import { formatBytes } from "../../lib/format";
import { ItemActions } from "./ItemActions";

const QUALITY: Record<Quality, [string, BadgeVariant]> = {
  better: ["Better", "good"],
  same: ["Same quality", "good"],
  worse: ["Worse", "bad"],
  unknown: ["Quality unknown", "neutral"],
};

const EDITION: Record<EditionCmp, [string, BadgeVariant] | null> = {
  same: null,
  different: ["Different edition", "bad"],
  unknown: null,
};

/** Movie detail: your copy against each other server's copy, then the item actions. */
export function MovieDetail({ detail }: { detail: FindingDetail }) {
  const copies = detail.copies ?? [];
  const th = "px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-400";
  const td = "px-3 py-2";
  const dash = <span className="text-slate-600">—</span>;
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-lg border border-slate-700">
        <table aria-label="Copies" className="w-full text-sm">
          <thead className="bg-slate-800/60">
            <tr>
              <th className={th}>Server</th>
              <th className={th}>Resolution</th>
              <th className={th}>HDR</th>
              <th className={th}>Codec</th>
              <th className={th}>Edition</th>
              <th className={th}>Size</th>
              <th className={th}>Vs yours</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            <tr className="bg-sky-950/30">
              <td className={`${td} font-medium text-sky-300`}>Yours</td>
              <td className={td}>
                {detail.type === "wanted_elsewhere" ? "Wanted" : (detail.yours.resolution ?? "?")}
              </td>
              <td className={td}>{dash}</td>
              <td className={td}>{dash}</td>
              <td className={td}>{dash}</td>
              <td className={td}>
                {detail.yours.size != null ? formatBytes(detail.yours.size) : dash}
              </td>
              <td className={td} />
            </tr>
            {copies.map((c, i) => {
              const [qText, qVariant] = QUALITY[c.quality];
              const edition = EDITION[c.edition_cmp];
              const notes = [
                c.stale && "stale: this server was not reachable at the last check",
                c.ignored && "ignored for this finding",
              ].filter(Boolean);
              return (
                <tr
                  key={`${c.server_id}-${i}`}
                  title={notes.join("; ") || undefined}
                  className={c.stale ? "opacity-50" : undefined}
                >
                  <td className={`${td} text-slate-100`}>
                    <span className={c.ignored ? "line-through" : undefined}>{c.label}</span>
                    {c.stale && <span className="ml-2 text-xs text-slate-400">stale</span>}
                    {c.ignored && <span className="ml-2 text-xs text-slate-400">ignored</span>}
                  </td>
                  <td className={td}>{c.resolution ?? "?"}</td>
                  <td className={td}>{c.hdr ? "HDR" : dash}</td>
                  <td className={td}>{c.codec ?? dash}</td>
                  <td className={td}>{c.edition ?? dash}</td>
                  <td className={td}>{c.size != null ? formatBytes(c.size) : dash}</td>
                  <td className={`${td} space-x-1`}>
                    <Badge variant={qVariant}>{qText}</Badge>
                    {edition && <Badge variant={edition[1]}>{edition[0]}</Badge>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <section
        aria-label="Actions"
        className="flex justify-end rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2"
      >
        <ItemActions finding={detail} />
      </section>
    </div>
  );
}
