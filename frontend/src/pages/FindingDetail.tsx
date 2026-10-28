import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { queryKeys } from "../api/queryClient";
import type { FindingDetail as Detail, HoldResult } from "../api/types";
import { DiffBadges } from "../components/DiffBadges";
import { useToast } from "../components/useToast";
import { History } from "../features/detail/History";
import { MovieDetail } from "../features/detail/MovieDetail";
import { SeasonGrid } from "../features/detail/SeasonGrid";
import { Poster, TypeLabel } from "../features/findings/FindingParts";
import { formatBytes } from "../lib/format";

export function FindingDetail() {
  const { id = "" } = useParams();
  const detail = useQuery({
    queryKey: queryKeys.findingDetail(id),
    queryFn: () => api.get<Detail>(`/findings/${id}`),
  });

  if (detail.isPending) return <p className="text-slate-400">Loading…</p>;
  if (detail.isError) {
    return (
      <p role="alert" className="text-red-300">
        {detail.error instanceof ApiError ? detail.error.detail : "Could not load this finding"}
      </p>
    );
  }
  const f = detail.data;
  return (
    <div className="space-y-5">
      <Link to="/findings" className="text-sm text-slate-400 hover:text-slate-200">
        ← Findings
      </Link>
      <Header detail={f} />
      {f.automation_hold && <HoldBanner id={f.id} />}
      {f.lifecycle !== "active" && (
        <p role="status" className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-300">
          This finding is {f.lifecycle}. Copies shown are from the last successful check.
        </p>
      )}
      {f.kind === "movie" ? <MovieDetail detail={f} /> : <SeasonGrid key={f.id} detail={f} />}
      <section className="space-y-2">
        <h2 className="text-lg font-semibold text-slate-100">History</h2>
        <History rows={f.history} />
      </section>
    </div>
  );
}

function Header({ detail: f }: { detail: Detail }) {
  const app = f.instance.type === "sonarr" ? "Sonarr" : "Radarr";
  return (
    <div className="flex gap-4">
      <Poster finding={f} className="h-36 w-24 flex-none" />
      <div className="min-w-0 space-y-1">
        <h1 className="text-2xl font-semibold text-slate-100">
          {f.title}
          {f.year != null && ` (${f.year})`}
        </h1>
        <p className="text-sm text-slate-400">
          {f.instance.name} · <TypeLabel finding={f} />
          {f.yours.size != null && ` · ${formatBytes(f.yours.size)} on disk`}
          {f.arr_url && (
            <>
              {" · "}
              <a href={f.arr_url} target="_blank" rel="noreferrer" className="underline hover:text-slate-200">
                Open in {app}
              </a>
            </>
          )}
        </p>
        <DiffBadges badges={f.badges} kind={f.kind} />
      </div>
    </div>
  );
}

function HoldBanner({ id }: { id: number }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const clear = useMutation({
    mutationFn: () => api.post<HoldResult>(`/findings/${id}/hold`, { hold: false }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.findings });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? error.detail : "Could not reach Overlaparr";
      toast(`Could not clear the hold: ${detail}`, "error");
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-3 rounded border border-amber-500/40 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
      <span>
        Automation is paused for this item because it was changed outside an automatic action (an
        undo, or a change in Sonarr/Radarr). Rules will not act on it until you clear the hold.
      </span>
      <button
        type="button"
        disabled={clear.isPending}
        onClick={() => clear.mutate()}
        className="ml-auto rounded border border-amber-500/60 px-3 py-1 hover:bg-amber-900/40 disabled:opacity-50"
      >
        Clear automation hold
      </button>
    </div>
  );
}
