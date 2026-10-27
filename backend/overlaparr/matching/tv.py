"""Match tracked Sonarr series to other servers' shows, then episodes to episodes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from overlaparr.domain.guids import ExternalIds
from overlaparr.domain.quality import Res, compare_quality
from overlaparr.domain.types import ComputedCopy, ComputedFinding, FindingType, MatchMethod

_LIVE = {"online", "degraded"}


def _res(value: int | None) -> Res | None:
    return Res(value) if value is not None else None


def _best_key(row: Any) -> tuple[int, int]:
    return (row.resolution if row.resolution is not None else -1, row.size or -1)


def _better(current: Any | None, row: Any) -> Any:
    return row if current is None or _best_key(row) > _best_key(current) else current


class ShowIndex:
    def __init__(self) -> None:
        self._tvdb: dict[int, dict[str, Any]] = {}
        self._tmdb: dict[int, dict[str, Any]] = {}
        self._imdb: dict[str, dict[str, Any]] = {}

    @classmethod
    def build(cls, rows: Iterable[Any], exclude_servers: Iterable[str] = frozenset()) -> ShowIndex:
        excluded = set(exclude_servers)
        index = cls()
        for row in rows:
            if row.kind != "show" or row.server_id in excluded:
                continue
            if row.tvdb is not None:
                index._tvdb.setdefault(row.tvdb, {}).setdefault(row.server_id, row)
            if row.tmdb is not None:
                index._tmdb.setdefault(row.tmdb, {}).setdefault(row.server_id, row)
            if row.imdb:
                index._imdb.setdefault(row.imdb, {}).setdefault(row.server_id, row)
        return index

    def lookup(self, ids: ExternalIds) -> dict[str, Any]:
        """Per server: TVDB match, else TMDB, else IMDb (lower priority only where higher missed)."""
        hits: dict[str, Any] = {}
        for table, key in ((self._tvdb, ids.tvdb), (self._tmdb, ids.tmdb), (self._imdb, ids.imdb)):
            if key is None or key == "":
                continue
            for server_id, row in table.get(key, {}).items():
                hits.setdefault(server_id, row)
        return hits


def match_series_shows(
    series: Iterable[Any], index: ShowIndex
) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for item in series:
        if item.kind != "series":
            continue
        hits = index.lookup(ExternalIds(tmdb=item.tmdb, tvdb=item.tvdb, imdb=item.imdb))
        if hits:
            result[(item.instance_id, item.arr_id)] = hits
    return result


def _match_server(
    episodes: list[Any], server_eps: list[Any]
) -> dict[int, tuple[Any, MatchMethod]]:
    """episode_arr_id -> (server episode, method) for one server.

    ID matches come first; number matching only uses server episodes no ID match consumed,
    and never re-matches an episode already matched by ID.
    """
    by_tvdb: dict[int, Any] = {}
    for row in server_eps:
        if row.tvdb_episode_id is not None:
            by_tvdb[row.tvdb_episode_id] = _better(by_tvdb.get(row.tvdb_episode_id), row)

    matches: dict[int, tuple[Any, MatchMethod]] = {}
    consumed: set[int] = set()
    for e in episodes:
        if e.tvdb_id is not None and e.tvdb_id in by_tvdb:
            matches[e.episode_arr_id] = (by_tvdb[e.tvdb_id], "id")
            consumed.add(e.tvdb_id)

    by_number: dict[tuple[int, int], Any] = {}
    for row in server_eps:
        if row.tvdb_episode_id is not None and row.tvdb_episode_id in consumed:
            continue
        key = (row.season, row.episode)
        by_number[key] = _better(by_number.get(key), row)

    for e in episodes:
        if e.episode_arr_id in matches:
            continue
        row = by_number.get((e.season, e.episode))
        if row is not None:
            matches[e.episode_arr_id] = (row, "number")
    return matches


def is_wanted_episode(e: Any, now: datetime) -> bool:
    """Monitored, missing and already aired."""
    return bool(
        e.monitored and not e.has_file and e.air_date_utc is not None and e.air_date_utc <= now
    )


def match_episodes(
    series: Any,
    episodes: list[Any],
    server_eps: dict[str, list[Any]],
    server_status: dict[str, str],
    cutoff: Res | None,
    now: datetime,
    keep_relevant: dict[tuple[str, int, str], list[int]] | None = None,
) -> list[ComputedFinding]:
    """``keep_relevant``: (instance_id, arr_id, type) of actioned findings -> their previous
    relevant episode ids; for such a type the relevant set is those ids that still exist."""
    if now.tzinfo is None:
        raise ValueError("naive datetime; use timezone-aware UTC")

    wanted = [e for e in episodes if is_wanted_episode(e, now)]
    on_disk = [e for e in episodes if e.has_file]

    # Match all tracked episodes so ID-claimed server episodes are consumed even when the
    # claiming episode is not itself relevant (e.g. unaired).
    per_server = {sid: _match_server(episodes, rows) for sid, rows in server_eps.items()}

    groups: list[tuple[FindingType, list[Any]]] = [("wanted_elsewhere", wanted), ("on_disk", on_disk)]
    by_id = {e.episode_arr_id: e for e in episodes}
    findings: list[ComputedFinding] = []
    for ftype, relevant in groups:
        kept = (keep_relevant or {}).get((series.instance_id, series.arr_id, ftype))
        if kept is not None:
            relevant = [by_id[i] for i in dict.fromkeys(kept) if i in by_id]
            if ftype == "wanted_elsewhere":
                # A downloaded episode is no longer wanted, even for an actioned finding.
                relevant = [e for e in relevant if not e.has_file]
        copies: list[ComputedCopy] = []
        for e in sorted(relevant, key=lambda e: e.episode_arr_id):
            if kept is not None:
                baseline = _res(e.resolution) if e.has_file else cutoff
            else:
                baseline = cutoff if ftype == "wanted_elsewhere" else _res(e.resolution)
            for sid, matches in per_server.items():
                hit = matches.get(e.episode_arr_id)
                if hit is None:
                    continue
                row, method = hit
                copies.append(
                    ComputedCopy(
                        server_id=sid,
                        episode_arr_id=e.episode_arr_id,
                        resolution=_res(row.resolution),
                        hdr=row.hdr,
                        codec=row.codec,
                        edition=None,
                        size=row.size,
                        quality=compare_quality(_res(row.resolution), baseline),
                        edition_cmp="unknown",
                        matched_by=method,
                        stale=server_status.get(sid) not in _LIVE,
                    )
                )
        if copies:
            findings.append(
                ComputedFinding(
                    instance_id=series.instance_id,
                    arr_id=series.arr_id,
                    kind="series",
                    type=ftype,
                    relevant_episode_ids=sorted(e.episode_arr_id for e in relevant),
                    copies=copies,
                )
            )
    return findings
