"""Server discovery and concurrent, budgeted indexing of other Plex servers."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from overlaparr.config.schema import ServerOverride
from overlaparr.db.models import IndexEpisode, IndexItem, Server, utcnow
from overlaparr.domain.guids import parse_guids
from overlaparr.domain.quality import parse_plex_resolution
from overlaparr.plex.gateway import (
    PlexConnection,
    PlexEpisodeRow,
    PlexGateway,
    PlexItemRow,
    PlexServerInfo,
    PlexUnreachable,
)

log = logging.getLogger(__name__)

Status = Literal["online", "degraded", "offline", "partial"]


@dataclass
class IndexResult:
    server_id: str
    status: Status
    items: int
    unmatchable: int
    error: str | None


def refresh_servers(
    db: Session, gateway: PlexGateway, home_id: str | None, overrides: list[ServerOverride]
) -> list[Server]:
    """Upsert servers discovered on plex.tv; return the enabled, discovered, non-home ones.

    Servers that vanished from discovery are never deleted (that would cascade-drop
    their index); they are marked ``offline`` and age to ``stale`` via ``mark_stale``.
    Raises ``PlexUnreachable`` when plex.tv cannot be listed.
    """
    discovered = {info.machine_id: info for info in gateway.list_servers()}
    enabled = {o.id: o.enabled for o in overrides}
    existing = {s.machine_id: s for s in db.scalars(select(Server))}
    for mid, info in discovered.items():
        srv = existing.get(mid)
        if srv is None:
            srv = Server(machine_id=mid, name=info.name, is_new=True, reachability="offline")
            db.add(srv)
            existing[mid] = srv
        else:
            srv.name = info.name
    for mid, srv in existing.items():
        srv.is_home = mid == home_id
        if mid not in discovered and srv.reachability in ("online", "degraded", "partial"):
            srv.reachability = "offline"
    db.commit()
    return [
        existing[mid]
        for mid in discovered
        if mid != home_id and enabled.get(mid, True)
    ]


def connect_server(gateway: PlexGateway, server: Server, timeout: float) -> PlexConnection | None:
    """Connect to a known server (looked up via plex.tv discovery); None if unreachable."""
    try:
        info = _discover(gateway).get(server.machine_id)
        if info is None:
            return None
        return gateway.connect(info, timeout)
    except PlexUnreachable as exc:
        log.info("plex server unreachable: %s (%s)", server.name, exc)
        return None


def _discover(gateway: PlexGateway) -> dict[str, PlexServerInfo]:
    return {info.machine_id: info for info in gateway.list_servers()}


@dataclass
class _Fetched:
    status: Status
    rows: list[PlexItemRow] = field(default_factory=list)
    error: str | None = None


def _fetch_server(
    gateway: PlexGateway,
    info: PlexServerInfo,
    timeout: float,
    page_size: int,
    cancel: threading.Event,
    progress: Callable[[str, int], None],
) -> _Fetched:
    """Worker: collect every item of a server into memory. Never touches the DB."""
    try:
        conn = gateway.connect(info, timeout)
    except Exception as exc:  # noqa: BLE001 - any connect failure means offline
        return _Fetched("offline", error=_err(exc))
    rows: list[PlexItemRow] = []
    try:
        for section_key, _kind in conn.sections():
            for page in conn.iter_items(section_key, page_size):
                if cancel.is_set():
                    return _Fetched("partial", error="index budget exceeded")
                rows.extend(page)
                progress(info.name, len(rows))
    except Exception as exc:  # noqa: BLE001 - mid-scan failure: keep previous index
        return _Fetched("partial", error=_err(exc))
    return _Fetched("degraded" if conn.via_relay else "online", rows=rows)


def _err(exc: Exception) -> str:
    # PlexUnreachable messages are token-free by construction; other types may not be.
    return str(exc) if isinstance(exc, PlexUnreachable) else type(exc).__name__


def _rank(row: IndexItem | IndexEpisode) -> tuple[int, int]:
    return (row.resolution if row.resolution is not None else -1, row.size or 0)


def _dedupe_items(server_id: str, rows: Iterable[PlexItemRow]) -> tuple[list[IndexItem], int]:
    """Build index rows: drop unmatchable items, keep the best copy per external id."""
    unmatchable = 0
    groups: list[list[IndexItem]] = []
    by_key: dict[tuple[str, str, object], int] = {}
    for row in rows:
        try:
            ids = parse_guids(row.guids)
            res = parse_plex_resolution(row.resolution) if row.kind == "movie" else None
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the index
            log.warning("server %s: skipping unreadable item %s (%s)", server_id,
                        getattr(row, "rating_key", "?"), type(exc).__name__)
            unmatchable += 1
            continue
        if ids.is_empty:
            unmatchable += 1
            continue
        item = IndexItem(
            server_id=server_id, kind=row.kind, rating_key=row.rating_key, title=row.title,
            year=row.year, tmdb=ids.tmdb, tvdb=ids.tvdb, imdb=ids.imdb,
            resolution=int(res) if res is not None else None, hdr=bool(row.hdr),
            codec=row.codec, edition=row.edition, size=row.size,
        )
        keys = [
            (row.kind, scheme, value)
            for scheme, value in (("tmdb", ids.tmdb), ("tvdb", ids.tvdb), ("imdb", ids.imdb))
            if value is not None
        ]
        hits = sorted({by_key[k] for k in keys if k in by_key})
        if not hits:
            groups.append([item])
            target = len(groups) - 1
        else:
            target = hits[0]
            groups[target].append(item)
            for other in hits[1:]:  # item links previously separate groups
                groups[target].extend(groups[other])
                groups[other] = []
                for k, g in by_key.items():
                    if g == other:
                        by_key[k] = target
        for k in keys:
            by_key[k] = target
    out: list[IndexItem] = []
    for group in groups:
        if not group:
            continue
        best = max(group, key=_rank)
        # Fill ids the best copy lacks from its duplicates so matching still finds it.
        for other in group:
            best.tmdb = best.tmdb if best.tmdb is not None else other.tmdb
            best.tvdb = best.tvdb if best.tvdb is not None else other.tvdb
            best.imdb = best.imdb if best.imdb is not None else other.imdb
        out.append(best)
    return out, unmatchable


def _record(
    db_factory: sessionmaker[Session], server_id: str, fetched: _Fetched, now: datetime
) -> IndexResult:
    """Persist one server's outcome in a single short transaction."""
    items: list[IndexItem] = []
    unmatchable = 0
    ok = fetched.status in ("online", "degraded")
    if ok:
        items, unmatchable = _dedupe_items(server_id, fetched.rows)
    with db_factory() as db:
        srv = db.get(Server, server_id)
        if srv is not None:
            srv.reachability = fetched.status
            srv.last_attempt_at = now
            if ok:
                db.execute(delete(IndexItem).where(IndexItem.server_id == server_id))
                db.execute(delete(IndexEpisode).where(IndexEpisode.server_id == server_id))
                db.add_all(items)
                srv.last_indexed_at = now
                srv.movie_count = sum(1 for i in items if i.kind == "movie")
                srv.show_count = sum(1 for i in items if i.kind == "show")
                srv.unmatchable_count = unmatchable
        db.commit()
    return IndexResult(server_id, fetched.status, len(items), unmatchable, fetched.error)


def _check_home(gateway: PlexGateway, info: PlexServerInfo, timeout: float) -> Status:
    """Worker: connect-only reachability check of the home server (never indexed)."""
    try:
        conn = gateway.connect(info, timeout)
    except Exception:  # noqa: BLE001 - any connect failure means offline
        return "offline"
    return "degraded" if conn.via_relay else "online"


def _record_home(
    db_factory: sessionmaker[Session], server_id: str, status: Status, now: datetime
) -> None:
    """Persist the home check; its index data and counts are left untouched."""
    with db_factory() as db:
        srv = db.get(Server, server_id)
        if srv is not None:
            srv.reachability = status
            srv.last_attempt_at = now
        db.commit()


def index_servers(
    db_factory: sessionmaker[Session],
    gateway: PlexGateway,
    servers: list[Server],
    timeout: float,
    budget_s: float,
    page_size: int,
    progress: Callable[[str, int], None],
    home_id: str | None = None,
) -> dict[str, IndexResult]:
    """Index every non-home server concurrently, each within ``budget_s``.

    Workers only fetch into memory. Results are persisted from this thread as each
    worker completes, so a slow server never delays others. A worker still running
    at the deadline is cancelled and reported ``partial``; its data is discarded.
    When ``home_id`` is given, the home server gets a connect-only check in the same
    pool; its reachability is persisted but it is never indexed nor returned.
    """
    targets = [
        (s.machine_id, s.name) for s in servers if not s.is_home and s.machine_id != home_id
    ]
    if not targets and home_id is None:
        return {}
    started = time.monotonic()
    results: dict[str, IndexResult] = {}
    try:
        discovered = _discover(gateway)
    except PlexUnreachable as exc:
        discovered, discovery_error = {}, str(exc)
    else:
        discovery_error = "server not found on plex.tv"

    cancel = threading.Event()
    pending: dict[Future[_Fetched], str] = {}
    home_fut: Future[Status] | None = None
    pool = ThreadPoolExecutor(max_workers=len(targets) + 1, thread_name_prefix="plex-index")
    try:
        if home_id is not None:
            home_info = discovered.get(home_id)
            if home_info is None:
                _record_home(db_factory, home_id, "offline", utcnow())
            else:
                home_fut = pool.submit(_check_home, gateway, home_info, timeout)
        for mid, _name in targets:
            info = discovered.get(mid)
            if info is None:
                results[mid] = _record(db_factory, mid, _Fetched("offline", error=discovery_error), utcnow())
                continue
            fut = pool.submit(_fetch_server, gateway, info, timeout, page_size, cancel, progress)
            pending[fut] = mid
        remaining = max(0.0, budget_s - (time.monotonic() - started))
        waiting: list[Future] = [*pending, *([home_fut] if home_fut is not None else [])]
        try:
            for fut in as_completed(waiting, timeout=remaining):
                if fut is home_fut:
                    _record_home(db_factory, home_id, fut.result(), utcnow())
                    home_fut = None
                    continue
                mid = pending.pop(fut)
                results[mid] = _record(db_factory, mid, fut.result(), utcnow())
        except FuturesTimeout:
            pass
    finally:
        cancel.set()
        pool.shutdown(wait=False, cancel_futures=True)
    if home_fut is not None:  # still connecting at the deadline
        _record_home(db_factory, home_id, "offline", utcnow())
    for mid in pending.values():
        results[mid] = _record(
            db_factory, mid, _Fetched("partial", error="index budget exceeded"), utcnow()
        )
    return results


def mark_stale(db: Session, stale_after_hours: int, now: datetime) -> None:
    """Servers not currently reachable whose index is older than the threshold → stale."""
    cutoff = now - timedelta(hours=stale_after_hours)
    for srv in db.scalars(select(Server)):
        if srv.reachability in ("online", "degraded"):
            continue
        if srv.last_indexed_at is not None and srv.last_indexed_at < cutoff:
            srv.reachability = "stale"
    db.commit()


def ensure_episodes(
    db: Session,
    conn: PlexConnection | None,
    server_id: str,
    show_rating_key: str,
    scan_started_at: datetime,
) -> tuple[list[IndexEpisode], bool]:
    """Return a show's episodes on a server, fetching lazily once per scan.

    Returns ``(rows, fresh)``. Cached rows all fetched at/after ``scan_started_at``
    are returned without refetching (fresh=True). When ``conn`` is None or the fetch
    fails, the cached rows (possibly empty) are returned with fresh=False and the
    session is left untouched; a failed DB write is rolled back and re-raised. A
    successful fetch atomically replaces the show's cached episodes, keeping one row
    per (season, episode): best resolution, then size.
    """
    def cached() -> list[IndexEpisode]:
        return list(
            db.scalars(
                select(IndexEpisode).where(
                    IndexEpisode.server_id == server_id,
                    IndexEpisode.show_rating_key == show_rating_key,
                )
            )
        )

    rows = cached()
    if rows and all(r.fetched_at >= scan_started_at for r in rows):
        return rows, True
    if conn is None:
        return rows, False
    try:
        new_rows = _episode_rows(conn.episodes(show_rating_key), server_id, show_rating_key)
    except Exception as exc:  # noqa: BLE001 - any fetch/parse failure: episodes not fetched
        log.info("episode fetch failed: server=%s show=%s (%s)", server_id, show_rating_key,
                 _err(exc))
        return rows, False
    try:
        db.execute(
            delete(IndexEpisode).where(
                IndexEpisode.server_id == server_id,
                IndexEpisode.show_rating_key == show_rating_key,
            )
        )
        db.add_all(new_rows)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return new_rows, True


def _episode_rows(
    fetched: Iterable[PlexEpisodeRow], server_id: str, show_rating_key: str
) -> list[IndexEpisode]:
    """One row per (season, episode): best resolution, then size."""
    now = utcnow()
    best: dict[tuple[int, int], IndexEpisode] = {}
    for e in fetched:
        if e.season is None or e.episode is None:
            continue
        res = parse_plex_resolution(e.resolution)
        row = IndexEpisode(
            server_id=server_id, show_rating_key=show_rating_key, season=e.season,
            episode=e.episode, tvdb_episode_id=parse_guids(e.guids).tvdb,
            resolution=int(res) if res is not None else None, hdr=bool(e.hdr),
            codec=e.codec, size=e.size, fetched_at=now,
        )
        key = (e.season, e.episode)
        if key not in best or _rank(row) > _rank(best[key]):
            best[key] = row
    return list(best.values())
