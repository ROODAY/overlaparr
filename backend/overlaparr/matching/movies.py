"""Match tracked Radarr movies against other servers' indexed movies by external id."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from overlaparr.domain.edition import compare_edition
from overlaparr.domain.guids import ExternalIds
from overlaparr.domain.quality import Res, compare_quality
from overlaparr.domain.types import ComputedCopy, ComputedFinding, FindingType

_LIVE = {"online", "degraded"}


def _res(value: int | None) -> Res | None:
    return Res(value) if value is not None else None


class MovieIndex:
    def __init__(self) -> None:
        self._tmdb: dict[int, dict[str, Any]] = {}
        self._imdb: dict[str, dict[str, Any]] = {}

    @classmethod
    def build(cls, rows: Iterable[Any], exclude_servers: Iterable[str] = frozenset()) -> MovieIndex:
        excluded = set(exclude_servers)
        index = cls()
        for row in rows:
            if row.kind != "movie" or row.server_id in excluded:
                continue
            if row.tmdb is not None:
                index._tmdb.setdefault(row.tmdb, {}).setdefault(row.server_id, row)
            if row.imdb:
                index._imdb.setdefault(row.imdb, {}).setdefault(row.server_id, row)
        return index

    def lookup(self, ids: ExternalIds) -> dict[str, Any]:
        """Per server: the TMDB match, else the IMDb match only if TMDB found nothing there."""
        hits: dict[str, Any] = dict(self._tmdb.get(ids.tmdb, {})) if ids.tmdb is not None else {}
        if ids.imdb:
            for server_id, row in self._imdb.get(ids.imdb, {}).items():
                hits.setdefault(server_id, row)
        return hits


def match_movies(
    items: Iterable[Any],
    index: MovieIndex,
    server_status: dict[str, str],
    ignore_tag_id: int | None,
    keep: Collection[tuple[str, int, str]] = frozenset(),
) -> list[ComputedFinding]:
    """``keep``: (instance_id, arr_id, type) of actioned findings to emit even when the
    arr-side precondition no longer holds (kept wanted still requires no file)."""
    findings: list[ComputedFinding] = []
    for item in items:
        if item.kind != "movie":
            continue
        if ignore_tag_id is not None and ignore_tag_id in (item.tag_ids or []):
            continue
        cutoff = _res(item.cutoff_resolution)
        wanted_kept = (item.instance_id, item.arr_id, "wanted_elsewhere") in keep
        on_disk_kept = (item.instance_id, item.arr_id, "on_disk") in keep
        groups: list[tuple[FindingType, Res | None, str | None]] = []
        if item.has_file:
            groups.append(("on_disk", _res(item.resolution), item.edition))
        else:
            if item.monitored or wanted_kept:
                groups.append(("wanted_elsewhere", cutoff, None))
            if on_disk_kept:
                groups.append(("on_disk", cutoff, None))
        if not groups:
            continue
        hits = index.lookup(ExternalIds(tmdb=item.tmdb, imdb=item.imdb))
        for ftype, baseline, own_edition in groups:
            finding = _finding(item, ftype, baseline, own_edition, hits, server_status)
            if finding is not None:
                findings.append(finding)
    return findings


def _finding(
    item: Any,
    ftype: FindingType,
    baseline: Res | None,
    own_edition: str | None,
    hits: dict[str, Any],
    server_status: dict[str, str],
) -> ComputedFinding | None:
    copies = [
        ComputedCopy(
            server_id=server_id,
            episode_arr_id=None,
            resolution=_res(row.resolution),
            hdr=row.hdr,
            codec=row.codec,
            edition=row.edition,
            size=row.size,
            quality=compare_quality(_res(row.resolution), baseline),
            edition_cmp=compare_edition(row.edition, own_edition),
            matched_by="id",
            stale=server_status.get(server_id) not in _LIVE,
        )
        for server_id, row in hits.items()
    ]
    if not copies:
        return None
    return ComputedFinding(
        instance_id=item.instance_id,
        arr_id=item.arr_id,
        kind="movie",
        type=ftype,
        copies=copies,
    )
