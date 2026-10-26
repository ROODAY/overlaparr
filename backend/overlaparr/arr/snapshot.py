"""Read a Sonarr/Radarr instance into plain data and persist it as tracked state."""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from overlaparr.arr.client import ArrError
from overlaparr.arr.radarr import RadarrClient
from overlaparr.arr.sonarr import SonarrClient
from overlaparr.config.schema import InstanceConfig
from overlaparr.db.models import TrackedEpisode, TrackedItem
from overlaparr.domain.quality import Res, profile_cutoff_resolution, res_from_height
from overlaparr.logging_setup import redact

log = logging.getLogger(__name__)

TIMEOUT = 30


@dataclass
class TrackedItemData:
    arr_id: int
    kind: str  # movie | series
    title: str
    year: int | None = None
    tmdb: int | None = None
    tvdb: int | None = None
    imdb: str | None = None
    monitored: bool = False
    has_file: bool = False
    file_size: int | None = None
    resolution: int | None = None
    edition: str | None = None
    cutoff_resolution: int | None = None
    quality_profile_id: int | None = None
    tag_ids: list[int] = field(default_factory=list)
    poster_url: str | None = None
    title_slug: str | None = None


@dataclass
class TrackedEpisodeData:
    episode_arr_id: int
    series_arr_id: int
    season: int
    episode: int
    title: str
    tvdb_id: int | None = None
    monitored: bool = False
    has_file: bool = False
    episode_file_id: int | None = None
    file_size: int | None = None
    resolution: int | None = None
    air_date_utc: datetime | None = None


@dataclass
class InstanceSnapshot:
    instance_id: str
    ok: bool
    error: str | None
    items: list[TrackedItemData] = field(default_factory=list)
    profiles: dict[int, Res | None] = field(default_factory=dict)
    tags: dict[int, str] = field(default_factory=dict)
    skipped_ids: set[int] = field(default_factory=set)  # malformed items; keep their rows


def make_client(inst: InstanceConfig) -> RadarrClient | SonarrClient:
    cls = RadarrClient if inst.type == "radarr" else SonarrClient
    return cls(inst.url.rstrip("/"), inst.api_key, timeout=TIMEOUT)


def _poster(images: list[dict] | None) -> str | None:
    for img in images or []:
        if isinstance(img, dict) and img.get("coverType") == "poster":
            return img.get("remoteUrl") or img.get("url")
    return None


def _res(quality_obj: dict | None) -> int | None:
    res = res_from_height(((quality_obj or {}).get("quality") or {}).get("resolution"))
    return None if res is None else int(res)


def _cutoff(profiles: dict[int, Res | None], pid: int | None) -> int | None:
    res = profiles.get(pid) if pid is not None else None
    return None if res is None else int(res)


def _movie(m: dict, profiles: dict[int, Res | None]) -> TrackedItemData:
    f = m.get("movieFile") or {}
    has_file = bool(m.get("hasFile"))
    pid = m.get("qualityProfileId")
    return TrackedItemData(
        arr_id=m["id"],
        kind="movie",
        title=m.get("title", ""),
        year=m.get("year") or None,
        tmdb=m.get("tmdbId") or None,
        imdb=m.get("imdbId") or None,
        monitored=bool(m.get("monitored")),
        has_file=has_file,
        file_size=f.get("size") if has_file else None,
        resolution=_res(f.get("quality")) if has_file else None,
        edition=(f.get("edition") or None) if has_file else None,
        cutoff_resolution=_cutoff(profiles, pid),
        quality_profile_id=pid,
        tag_ids=list(m.get("tags") or []),
        poster_url=_poster(m.get("images")),
        title_slug=m.get("titleSlug"),
    )


def _series(s: dict, profiles: dict[int, Res | None]) -> TrackedItemData:
    stats = s.get("statistics") or {}
    pid = s.get("qualityProfileId")
    return TrackedItemData(
        arr_id=s["id"],
        kind="series",
        title=s.get("title", ""),
        year=s.get("year") or None,
        tmdb=s.get("tmdbId") or None,
        tvdb=s.get("tvdbId") or None,
        imdb=s.get("imdbId") or None,
        monitored=bool(s.get("monitored")),
        has_file=(stats.get("episodeFileCount") or 0) > 0,
        file_size=stats.get("sizeOnDisk"),
        cutoff_resolution=_cutoff(profiles, pid),
        quality_profile_id=pid,
        tag_ids=list(s.get("tags") or []),
        poster_url=_poster(s.get("images")),
        title_slug=s.get("titleSlug"),
    )


def _safe_msg(e: Exception, api_key: str) -> str:
    msg = e.message if isinstance(e, ArrError) else f"{type(e).__name__}: {e}"
    if api_key:
        msg = msg.replace(api_key, "***")
    return redact(msg)


def _usable_id(raw: object) -> int | None:
    rid = raw.get("id") if isinstance(raw, dict) else None
    return rid if isinstance(rid, int) and not isinstance(rid, bool) else None


def read_instance(inst: InstanceConfig, client: RadarrClient | SonarrClient) -> InstanceSnapshot:
    """Read the whole instance; never raises (failure -> ok=False).

    A malformed profile, tag or item is logged and skipped; ids of skipped items are
    reported in ``skipped_ids`` so persisting does not delete their existing rows.
    """
    try:
        raw_profiles = client.quality_profiles()
        raw_tags = client.tags()
        raw_items = client.movies() if isinstance(client, RadarrClient) else client.series()
    except Exception as e:  # noqa: BLE001 - a bad instance must not abort the scan
        msg = _safe_msg(e, inst.api_key)
        log.warning("instance %s read failed: %s", inst.id, msg)
        return InstanceSnapshot(inst.id, False, msg)

    profiles: dict[int, Res | None] = {}
    for p in raw_profiles:
        try:
            profiles[p["id"]] = profile_cutoff_resolution(p)
        except Exception as e:  # noqa: BLE001
            log.warning("instance %s: skipping malformed profile: %s", inst.id, _safe_msg(e, inst.api_key))
    tags: dict[int, str] = {}
    for t in raw_tags:
        try:
            tags[t["id"]] = t["label"]
        except Exception as e:  # noqa: BLE001
            log.warning("instance %s: skipping malformed tag: %s", inst.id, _safe_msg(e, inst.api_key))

    mapper = _movie if isinstance(client, RadarrClient) else _series
    items: list[TrackedItemData] = []
    skipped: set[int] = set()
    for raw in raw_items:
        try:
            items.append(mapper(raw, profiles))
        except Exception as e:  # noqa: BLE001
            rid = _usable_id(raw)
            if rid is not None:
                skipped.add(rid)
            log.warning(
                "instance %s: skipping malformed item %s: %s",
                inst.id, rid, _safe_msg(e, inst.api_key),
            )
    return InstanceSnapshot(inst.id, True, None, items, profiles, tags, skipped)


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def read_series_episodes(
    client: SonarrClient, series_id: int
) -> tuple[list[TrackedEpisodeData], set[int]]:
    """Return (episodes, skipped_ids); malformed episodes/files are logged and skipped."""
    files: dict[int, dict] = {}
    for f in client.episode_files(series_id):
        fid = _usable_id(f)
        if fid is None:
            log.warning("series %s: skipping malformed episode file", series_id)
            continue
        files[fid] = f
    out: list[TrackedEpisodeData] = []
    skipped: set[int] = set()
    for e in client.episodes(series_id):
        try:
            fid = e.get("episodeFileId") or None
            f = files.get(fid) if fid else None
            out.append(
                TrackedEpisodeData(
                    episode_arr_id=e["id"],
                    series_arr_id=e.get("seriesId", series_id),
                    season=e["seasonNumber"],
                    episode=e["episodeNumber"],
                    title=e.get("title", ""),
                    tvdb_id=e.get("tvdbId") or None,
                    monitored=bool(e.get("monitored")),
                    has_file=bool(e.get("hasFile")),
                    episode_file_id=fid,
                    file_size=f.get("size") if f else None,
                    resolution=_res(f.get("quality")) if f else None,
                    air_date_utc=_parse_dt(e.get("airDateUtc")),
                )
            )
        except Exception as ex:  # noqa: BLE001
            rid = _usable_id(e)
            if rid is not None:
                skipped.add(rid)
            log.warning("series %s: skipping malformed episode %s: %s", series_id, rid, redact(str(ex)))
    return out, skipped


_ITEM_FIELDS = (
    "kind", "title", "year", "tmdb", "tvdb", "imdb", "monitored", "has_file",
    "file_size", "resolution", "edition", "cutoff_resolution", "quality_profile_id",
    "poster_url", "title_slug",
)  # fmt: skip


def persist_snapshot(db: Session, snap: InstanceSnapshot, scan_id: int) -> None:
    """Upsert items; delete vanished ones only when the snapshot was successful."""
    if not snap.ok:
        return
    existing = {
        r.arr_id: r
        for r in db.scalars(select(TrackedItem).where(TrackedItem.instance_id == snap.instance_id))
    }
    seen: set[int] = set()
    for d in snap.items:
        seen.add(d.arr_id)
        row = existing.get(d.arr_id)
        if row is None:
            row = TrackedItem(instance_id=snap.instance_id, arr_id=d.arr_id)
            db.add(row)
        for name in _ITEM_FIELDS:
            setattr(row, name, getattr(d, name))
        row.tag_ids = list(d.tag_ids)
        row.seen_scan_id = scan_id
    for arr_id, row in existing.items():
        if arr_id not in seen and arr_id not in snap.skipped_ids:
            db.delete(row)
    db.commit()


def persist_episodes(
    db: Session,
    instance_id: str,
    series_arr_id: int,
    episodes: list[TrackedEpisodeData],
    keep_ids: Collection[int] = (),
) -> None:
    """Replace one series' episode rows atomically; rows whose episode id is in keep_ids stay."""
    try:
        db.execute(
            delete(TrackedEpisode).where(
                TrackedEpisode.instance_id == instance_id,
                TrackedEpisode.series_arr_id == series_arr_id,
                TrackedEpisode.episode_arr_id.not_in(list(keep_ids)),
            )
        )
        db.add_all(
            TrackedEpisode(
                instance_id=instance_id,
                series_arr_id=series_arr_id,
                episode_arr_id=e.episode_arr_id,
                season=e.season,
                episode=e.episode,
                title=e.title,
                tvdb_id=e.tvdb_id,
                monitored=e.monitored,
                has_file=e.has_file,
                episode_file_id=e.episode_file_id,
                file_size=e.file_size,
                resolution=e.resolution,
                air_date_utc=e.air_date_utc,
            )
            for e in episodes
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
