"""Shared statistics helpers (used by notifications and the dashboard)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.db.models import Finding, TrackedEpisode, TrackedItem


def reclaimable_bytes(db: Session) -> int:
    """Bytes on disk for active, non-ignored on_disk findings (movie file / episode sums)."""
    findings = db.scalars(select(Finding).where(
        Finding.type == "on_disk", Finding.lifecycle == "active",
        Finding.review_state != "ignored"))
    total = 0
    for f in findings:
        if f.kind == "movie":
            total += db.scalar(select(func.coalesce(TrackedItem.file_size, 0)).where(
                TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id)) or 0
        elif f.relevant_episode_ids:
            total += db.scalar(select(func.coalesce(func.sum(TrackedEpisode.file_size), 0)).where(
                TrackedEpisode.instance_id == f.instance_id,
                TrackedEpisode.episode_arr_id.in_(f.relevant_episode_ids))) or 0
    return int(total)


def format_bytes(n: int) -> str:
    """Human-readable size, e.g. ``1.8 TB`` (1024-based units)."""
    size = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover
