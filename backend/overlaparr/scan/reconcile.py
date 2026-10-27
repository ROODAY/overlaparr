"""Reconcile computed findings with persisted ones, without false losses."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from overlaparr.db.models import (
    Finding,
    FindingCopy,
    FindingServerIgnore,
    TrackedEpisode,
    TrackedItem,
)
from overlaparr.domain.types import ComputedFinding

_LIVE = {"online", "degraded"}
_QUALITY_ORDER = ("better", "same", "worse", "unknown")


@dataclass
class ReconcileResult:
    new_ids: list[int] = field(default_factory=list)
    lost_ids: list[int] = field(default_factory=list)
    removed_ids: list[int] = field(default_factory=list)
    updated_ids: list[int] = field(default_factory=list)


def _summarize(f: Finding, copies: list[FindingCopy], ignored_servers: set[str]) -> None:
    counted = [c for c in copies if c.server_id not in ignored_servers]
    if f.kind == "movie":
        f.relevant_count = 1
        f.covered_count = 1 if counted else 0
    else:
        relevant = set(f.relevant_episode_ids)
        f.relevant_count = len(relevant)
        f.covered_count = len(relevant & {c.episode_arr_id for c in counted})
    f.coverage = "full" if f.covered_count >= f.relevant_count else "partial"
    qualities = {c.quality for c in copies}
    f.best_quality = next((q for q in _QUALITY_ORDER if q in qualities), "unknown")
    editions = {c.edition_cmp for c in copies}
    f.edition_summary = (
        "different" if "different" in editions else "same" if "same" in editions else "unknown"
    )
    f.has_number_matches = any(c.matched_by == "number" for c in copies)


def _purge_instances(db: Session, configured: set[str]) -> None:
    keep = list(configured)
    # FK cascades remove copies, server ignores and rule states with the findings.
    db.execute(delete(Finding).where(Finding.instance_id.not_in(keep)))
    db.execute(delete(TrackedItem).where(TrackedItem.instance_id.not_in(keep)))
    db.execute(delete(TrackedEpisode).where(TrackedEpisode.instance_id.not_in(keep)))


def reconcile(
    db: Session,
    computed: list[ComputedFinding],
    scan_id: int,
    server_status: dict[str, str],
    instance_ok: dict[str, bool],
    configured_instances: set[str],
    ignored_items: set[tuple[str, int]],
) -> ReconcileResult:
    """Apply one scan's computed findings to the DB in a single transaction."""
    try:
        result = _reconcile(
            db, computed, scan_id, server_status, instance_ok, configured_instances,
            ignored_items,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _reconcile(
    db: Session,
    computed: list[ComputedFinding],
    scan_id: int,
    server_status: dict[str, str],
    instance_ok: dict[str, bool],
    configured_instances: set[str],
    ignored_items: set[tuple[str, int]],
) -> ReconcileResult:
    result = ReconcileResult()
    _purge_instances(db, configured_instances)
    ok_instances = [i for i in configured_instances if instance_ok.get(i) is True]
    if not ok_instances:
        return result

    def reachable(server_id: str) -> bool:
        return server_status.get(server_id) in _LIVE

    existing = {
        (f.instance_id, f.arr_id, f.type): f
        for f in db.scalars(select(Finding).where(Finding.instance_id.in_(ok_instances)))
    }
    ids = [f.id for f in existing.values()]
    old_copies: dict[int, list[FindingCopy]] = defaultdict(list)
    server_ignores: dict[int, set[str]] = defaultdict(set)
    if ids:
        for c in db.scalars(select(FindingCopy).where(FindingCopy.finding_id.in_(ids))):
            old_copies[c.finding_id].append(c)
        for ig in db.scalars(
            select(FindingServerIgnore).where(FindingServerIgnore.finding_id.in_(ids))
        ):
            server_ignores[ig.finding_id].add(ig.server_id)
    tracked = {
        (inst, arr_id)
        for inst, arr_id in db.execute(
            select(TrackedItem.instance_id, TrackedItem.arr_id).where(
                TrackedItem.instance_id.in_(ok_instances)
            )
        )
    }

    seen: set[tuple[str, int, str]] = set()
    new_findings: list[Finding] = []
    for cf in computed:
        if cf.instance_id not in ok_instances:
            continue
        key = (cf.instance_id, cf.arr_id, cf.type)
        seen.add(key)
        f = existing.get(key)
        if f is None:
            f = Finding(
                instance_id=cf.instance_id, arr_id=cf.arr_id, kind=cf.kind, type=cf.type,
                review_state="new", lifecycle="active", first_seen_scan_id=scan_id,
                last_seen_scan_id=scan_id, coverage="partial", best_quality="unknown",
                edition_summary="unknown",
            )
            db.add(f)
            db.flush()
            new_findings.append(f)
        else:
            for c in old_copies.pop(f.id, []):
                db.delete(c)
            f.lifecycle = "active"
            f.last_seen_scan_id = scan_id
            result.updated_ids.append(f.id)
        f.relevant_episode_ids = list(cf.relevant_episode_ids)
        rows = [
            FindingCopy(
                finding_id=f.id,
                server_id=c.server_id,
                episode_arr_id=c.episode_arr_id,
                resolution=int(c.resolution) if c.resolution is not None else None,
                hdr=c.hdr,
                codec=c.codec,
                edition=c.edition,
                size=c.size,
                quality=c.quality,
                edition_cmp=c.edition_cmp,
                matched_by=c.matched_by,
                stale=c.stale or not reachable(c.server_id),
            )
            for c in cf.copies
        ]
        db.add_all(rows)
        _summarize(f, rows, server_ignores.get(f.id, set()))
        if (f.instance_id, f.arr_id) in ignored_items:
            f.review_state = "ignored"

    for key, f in existing.items():
        if key in seen:
            continue
        if (f.instance_id, f.arr_id) not in tracked:
            if f.lifecycle != "removed":
                f.lifecycle = "removed"
                result.removed_ids.append(f.id)
        elif f.lifecycle == "active":
            prior = old_copies.get(f.id, [])
            if all(reachable(c.server_id) for c in prior):
                f.lifecycle = "lost"
                for c in prior:
                    db.delete(c)
                _summarize(f, [], server_ignores.get(f.id, set()))
                result.lost_ids.append(f.id)
            else:
                for c in prior:
                    c.stale = True
                result.updated_ids.append(f.id)
        if (f.instance_id, f.arr_id) in ignored_items:
            f.review_state = "ignored"

    db.flush()
    result.new_ids = [f.id for f in new_findings]
    return result
