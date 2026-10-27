"""Shared statistics helpers (used by notifications and the dashboard)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.db.models import Finding, TrackedEpisode, TrackedItem


def file_key(ep: TrackedEpisode) -> tuple:
    """Identity of an episode's file: several episodes can share one (multi-episode files)."""
    if ep.episode_file_id is not None:
        return (ep.instance_id, "file", ep.episode_file_id)
    return (ep.instance_id, "episode", ep.episode_arr_id)


def episode_files_bytes(episodes: Iterable[TrackedEpisode]) -> int:
    """Total size of the episodes' files, each shared file counted once."""
    return sum({file_key(e): e.file_size or 0 for e in episodes}.values())


def reclaimable_bytes(db: Session) -> int:
    """Bytes on disk for active, non-ignored on_disk findings (movie file / episode files)."""
    findings = db.scalars(select(Finding).where(
        Finding.type == "on_disk", Finding.lifecycle == "active",
        Finding.review_state != "ignored"))
    total = 0
    for f in findings:
        if f.kind == "movie":
            total += db.scalar(select(func.coalesce(TrackedItem.file_size, 0)).where(
                TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id)) or 0
        elif f.relevant_episode_ids:
            total += episode_files_bytes(db.scalars(select(TrackedEpisode).where(
                TrackedEpisode.instance_id == f.instance_id,
                TrackedEpisode.episode_arr_id.in_(f.relevant_episode_ids))))
    return int(total)


def format_bytes(n: int) -> str:
    """Human-readable size, e.g. ``1.8 TB`` (1024-based units)."""
    size = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover
