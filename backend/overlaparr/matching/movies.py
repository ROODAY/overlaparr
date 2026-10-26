"""Match tracked Radarr movies against other servers' indexed movies by external id."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from overlaparr.domain.edition import compare_edition
from overlaparr.domain.guids import ExternalIds
from overlaparr.domain.quality import Res, compare_quality
from overlaparr.domain.types import ComputedCopy, ComputedFinding

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
) -> list[ComputedFinding]:
    findings: list[ComputedFinding] = []
    for item in items:
        if item.kind != "movie":
            continue
        if ignore_tag_id is not None and ignore_tag_id in (item.tag_ids or []):
            continue
        if item.has_file:
            ftype, baseline, own_edition = "on_disk", _res(item.resolution), item.edition
        elif item.monitored:
            ftype, baseline, own_edition = "wanted_elsewhere", _res(item.cutoff_resolution), None
        else:
            continue
        hits = index.lookup(ExternalIds(tmdb=item.tmdb, imdb=item.imdb))
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
        if copies:
            findings.append(
                ComputedFinding(
                    instance_id=item.instance_id,
                    arr_id=item.arr_id,
                    kind="movie",
                    type=ftype,
                    copies=copies,
                )
            )
    return findings
