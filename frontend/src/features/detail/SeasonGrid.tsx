import { useState } from "react";
import type { EpisodeRow, FindingDetail } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { DeleteDialog } from "../findings/DeleteDialog";
import { useRunActions } from "../findings/useRunActions";
import { EpisodeTable, type ServerColumn } from "./EpisodeTable";
import { ItemActions } from "./ItemActions";
import { BUTTON, DANGER_BUTTON } from "./styles";
import { useEpisodeSelection } from "./useEpisodeSelection";

/** Covered = some server that isn't ignored for this finding has a copy. */
function coveredIds(episodes: EpisodeRow[], ignored: ReadonlySet<string>): number[] {
  return episodes
    .filter((e) => Object.keys(e.copies).some((sid) => !ignored.has(sid)))
    .map((e) => e.episode_arr_id);
}

/**
 * TV detail: season buttons, the selected season's episode table (one column per server
 * with at least one copy in that season) and the selection + series action bar.
 */
export function SeasonGrid({ detail }: { detail: FindingDetail }) {
  const episodes = detail.episodes ?? [];
  const seasons = detail.seasons ?? [];
  const ignored = new Set(detail.elsewhere.filter((e) => e.ignored).map((e) => e.server_id));
  const labels = new Map(detail.elsewhere.map((e) => [e.server_id, e.label]));
  const selection = useEpisodeSelection(coveredIds(episodes, ignored), episodes);
  const [season, setSeason] = useState(
    () => (seasons.find((s) => s.covered > 0) ?? seasons[0])?.season ?? null,
  );

  const inSeason = episodes.filter((e) => e.season === season);
  const servers: ServerColumn[] = [...new Set(inSeason.flatMap((e) => Object.keys(e.copies)))]
    .map((id) => ({ id, label: labels.get(id) ?? id }))
    .sort((a, b) => a.label.localeCompare(b.label));

  return (
    <div className="space-y-3">
      <div role="group" aria-label="Seasons" className="flex flex-wrap gap-2">
        {seasons.map((s) => (
          <button
            key={s.season}
            type="button"
            aria-pressed={s.season === season}
            onClick={() => setSeason(s.season)}
            className={`rounded-md px-3 py-1.5 text-left text-sm leading-tight ${
              s.season === season
                ? "bg-accent text-white"
                : "bg-slate-800 text-slate-200 hover:bg-slate-700"
            }`}
          >
            {s.season === 0 ? "Specials" : `Season ${s.season}`}{" "}
            <small className="block text-xs opacity-80">
              {s.covered}/{s.relevant} covered
              {s.servers.length > 0 && ` · ${s.servers.join(", ")}`}
            </small>
          </button>
        ))}
      </div>
      {inSeason.length > 0 && (
        <EpisodeTable
          episodes={inSeason}
          servers={servers}
          selected={selection.selected}
          onToggle={selection.toggle}
        />
      )}
      <ActionBar
        detail={detail}
        selection={selection}
        seasonIds={inSeason.map((e) => e.episode_arr_id)}
      />
    </div>
  );
}

function ActionBar({
  detail,
  selection,
  seasonIds,
}: {
  detail: FindingDetail;
  selection: ReturnType<typeof useEpisodeSelection>;
  seasonIds: number[];
}) {
  const [deleting, setDeleting] = useState(false);
  const mutation = useRunActions();
  const ids = (detail.episodes ?? [])
    .map((e) => e.episode_arr_id)
    .filter((id) => selection.selected.has(id));
  const target = { finding_id: detail.id, scope: "episodes" as const, episode_ids: ids };
  const link = "rounded px-2 py-0.5 text-slate-300 hover:bg-slate-700";

  return (
    <section
      aria-label="Actions"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm"
    >
      <span className="font-medium text-slate-100">
        {ids.length} selected
        {/* Approximate: a multi-episode file counts per episode; the dialog is exact. */}
        {selection.bytes > 0 && ` · ≈${formatBytes(selection.bytes)}`}
      </span>
      <span className="text-slate-400">Select:</span>
      <button type="button" className={link} onClick={selection.selectCovered}>
        Covered
      </button>
      <button type="button" className={link} onClick={() => selection.selectAll(seasonIds)}>
        All in season
      </button>
      <button type="button" className={link} onClick={selection.clear}>
        None
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={ids.length === 0 || mutation.isPending}
        onClick={() =>
          mutation.mutate({
            label: "Unmonitor selected",
            confirm: false,
            requests: [{ ...target, action: "unmonitor" }],
          })
        }
      >
        Unmonitor selected
      </button>
      <button
        type="button"
        className={DANGER_BUTTON}
        disabled={ids.length === 0 || mutation.isPending}
        onClick={() => setDeleting(true)}
      >
        Delete selected files…
      </button>
      <span className="flex-1" />
      <ItemActions finding={detail} />
      {deleting && (
        <DeleteDialog
          title="Delete selected episode files"
          targets={[{ ...target, title: detail.title }]}
          onCancel={() => setDeleting(false)}
          onConfirm={(requests) => {
            setDeleting(false);
            mutation.mutate({ label: "Delete", requests, confirm: true });
          }}
        />
      )}
    </section>
  );
}
