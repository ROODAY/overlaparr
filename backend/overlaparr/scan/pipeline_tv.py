"""Series step of a scan: match shows, refresh episodes on both sides, match episodes."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.arr.snapshot import persist_episodes, read_series_episodes
from overlaparr.arr.sonarr import SonarrClient
from overlaparr.db.models import IndexEpisode, TrackedEpisode, TrackedItem
from overlaparr.domain.quality import Res
from overlaparr.domain.types import ComputedFinding
from overlaparr.matching.tv import ShowIndex, match_episodes, match_series_shows
from overlaparr.plex.gateway import PlexConnection
from overlaparr.plex.indexer import ensure_episodes

_LIVE = {"online", "degraded"}


def match_instance_series(
    db: Session,
    client: SonarrClient,
    instance_id: str,
    series: Iterable[TrackedItem],
    index: ShowIndex,
    server_status: dict[str, str],
    keep_relevant: dict[tuple[str, int, str], list[int]],
    scan_started: datetime,
    connect: Callable[[str], PlexConnection | None],
    unfetched: set[tuple[str, int, str]] | None = None,
) -> list[ComputedFinding]:
    """Findings for one Sonarr instance's (non-ignored) series.

    Only series matched to a show elsewhere have their Sonarr episodes read and their
    server episode lists ensured. ``connect(server_id)`` returns a per-scan cached
    connection (None when unreachable: cached episodes are used). Raises ``ArrError``
    when Sonarr fails mid-way; the caller decides what that means for the instance.

    A live server whose episodes for a show could not be fetched this scan contributes
    no copies; ``(instance_id, arr_id, server_id)`` is added to ``unfetched`` so
    reconcile keeps that server's previous copies (stale) instead of treating them as
    gone. Servers that are not live keep matching against their cached episodes.
    """
    by_id = {s.arr_id: s for s in series if s.kind == "series"}
    findings: list[ComputedFinding] = []
    for (_inst, arr_id), hits in match_series_shows(by_id.values(), index).items():
        item = by_id[arr_id]
        episodes, skipped = read_series_episodes(client, arr_id)
        persist_episodes(db, instance_id, arr_id, episodes, keep_ids=skipped)
        tracked = list(
            db.scalars(
                select(TrackedEpisode).where(
                    TrackedEpisode.instance_id == instance_id,
                    TrackedEpisode.series_arr_id == arr_id,
                )
            )
        )
        server_eps: dict[str, list[IndexEpisode]] = {}
        for sid, show in hits.items():
            rows, fresh = ensure_episodes(db, connect(sid), sid, show.rating_key, scan_started)
            if server_status.get(sid) in _LIVE and not fresh:
                if unfetched is not None:
                    unfetched.add((instance_id, arr_id, sid))
                continue
            server_eps[sid] = rows
        cutoff = Res(item.cutoff_resolution) if item.cutoff_resolution is not None else None
        findings += match_episodes(
            item, tracked, server_eps, server_status, cutoff, scan_started, keep_relevant
        )
    return findings
