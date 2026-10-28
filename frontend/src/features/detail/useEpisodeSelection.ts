import { useState } from "react";
import type { EpisodeRow } from "../../api/types";

/**
 * Episode checkbox state for the season grid. It starts on `initial` (the covered
 * episodes) and is kept across season switches; `bytes` sums your selected files.
 */
export function useEpisodeSelection(initial: number[], episodes: EpisodeRow[] = []) {
  const [selected, setSelected] = useState(() => new Set(initial));

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const selectCovered = () => setSelected(new Set(initial));
  const selectAll = (ids: number[]) => setSelected((prev) => new Set([...prev, ...ids]));
  const clear = () => setSelected(new Set());

  const bytes = episodes.reduce(
    (sum, e) => (selected.has(e.episode_arr_id) ? sum + (e.yours.size ?? 0) : sum),
    0,
  );
  return { selected, toggle, selectCovered, selectAll, clear, bytes };
}
