"""Loss handling: react to findings whose other-server copies disappeared.

Runs as a post-scan hook before rules. It only ever touches findings reconcile
just marked lost (``ctx.result.lost_ids``), never anything on an offline server.
Every step is optional per instance (``loss_handling``) and logged as ``auto``.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.actions.borrowed import client_for as borrowed_client_for
from overlaparr.actions.executor import find_instance
from overlaparr.actions.undo import UndoError, undo
from overlaparr.arr.client import ArrError
from overlaparr.config.schema import InstanceConfig
from overlaparr.db.models import ActionLog, Finding, FindingCopy, TrackedItem
from overlaparr.logging_setup import redact
from overlaparr.notify.events import queue_event
from overlaparr.scan.engine import ScanContext

log = logging.getLogger(__name__)

RULE_NAME = "loss handling"
_MONITORING_ACTIONS = ("unmonitor", "delete")


def _pending(db: Session, f: Finding, actions: tuple[str, ...]) -> list[ActionLog]:
    """Un-undone, undoable rows for the finding (originals only, not undo rows)."""
    return list(db.scalars(select(ActionLog).where(
        ActionLog.finding_id == f.id, ActionLog.action.in_(actions),
        ActionLog.undone_at.is_(None), ActionLog.undo_of_id.is_(None),
        ActionLog.undoable.is_(True), ActionLog.result.in_(("ok", "error")),
    ).order_by(ActionLog.id)))


def _record(ctx: ScanContext, f: Finding, action: str, scope: dict, prior: dict,
            result: str, message: str) -> None:
    ctx.db.add(ActionLog(
        actor="auto", rule_name=RULE_NAME, scan_run_id=ctx.scan_id,
        instance_id=f.instance_id, instance_name=_instance_name(ctx, f),
        finding_id=f.id, arr_id=f.arr_id, item_title=_title(ctx.db, f), action=action,
        scope=scope, prior_state=prior, result=result, message=message, undoable=False,
    ))
    ctx.db.commit()


def _instance_name(ctx: ScanContext, f: Finding) -> str:
    inst = find_instance(ctx.config, f.instance_id)
    return inst.name if inst is not None else f.instance_id


def _item(db: Session, f: Finding) -> TrackedItem | None:
    return db.scalar(select(TrackedItem).where(
        TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id))


def _title(db: Session, f: Finding) -> str:
    item = _item(db, f)
    return item.title if item is not None else str(f.arr_id)


def _undo_rows(ctx: ScanContext, f: Finding, rows: list[ActionLog],
               client_for) -> tuple[bool, list[int]]:
    """Undo each row; returns (any undone ok, ids that were set back to monitored)."""
    any_ok, remonitored = False, []
    for orig in rows:
        scope, prior = dict(orig.scope), dict(orig.prior_state)
        try:
            row = undo(ctx.db, ctx.config, orig.id, "auto", client_for)
        except UndoError as e:
            log.warning("loss handling: cannot undo action %s: %s", orig.id, redact(str(e)))
            continue
        row.rule_name, row.scan_run_id = RULE_NAME, ctx.scan_id
        ctx.db.commit()
        if row.result != "ok":
            continue
        any_ok = True
        if f.kind == "movie" or scope.get("scope") == "episodes":  # not series-level ids
            remonitored += [int(k) for k, v in (prior.get("monitored") or {}).items() if v is True]
    return any_ok, remonitored


def _handle(ctx: ScanContext, f: Finding, inst: InstanceConfig) -> list[str]:
    lh, db = inst.loss_handling, ctx.db
    client_for = borrowed_client_for(ctx.clients)
    taken: list[str] = []
    remonitored: list[int] = []

    if lh.remonitor:
        rows = [r for r in _pending(db, f, _MONITORING_ACTIONS)
                if r.prior_state.get("monitored")]
        done, remonitored = _undo_rows(ctx, f, rows, client_for)
        if done:
            taken.append("remonitor")
    if lh.remove_exclusion:
        done, _ = _undo_rows(ctx, f, _pending(db, f, ("exclude",)), client_for)
        if done:
            taken.append("remove_exclusion")

    client = ctx.clients.get(inst.id)
    if client is None:
        return taken
    if lh.quality_profile_id is not None:
        item = _item(db, f)
        prior = {"quality_profile_id": item.quality_profile_id if item is not None else None}
        scope = {"profile_id": lh.quality_profile_id}
        try:
            client.set_quality_profile([f.arr_id], lh.quality_profile_id)
        except ArrError as e:
            _record(ctx, f, "quality_profile", scope, prior, "error", redact(str(e)))
        else:
            _record(ctx, f, "quality_profile", scope, prior, "ok",
                    f"quality profile set to {lh.quality_profile_id}")
            taken.append("quality_profile")
    if lh.search:
        if f.kind == "movie":
            ids = [f.arr_id]
        else:
            ids = sorted(set(remonitored)) or list(f.relevant_episode_ids)
        scope = {"ids": ids}
        try:
            client.search(ids) if f.kind == "movie" else client.search_episodes(ids)
        except ArrError as e:
            _record(ctx, f, "search", scope, {}, "error", redact(str(e)))
        else:
            _record(ctx, f, "search", scope, {}, "ok", f"searched {len(ids)} item(s)")
            taken.append("search")
    return taken


def _notify(ctx: ScanContext, f: Finding, taken: list[str]) -> None:
    servers = sorted({c.server_id for c in ctx.db.scalars(
        select(FindingCopy).where(FindingCopy.finding_id == f.id))})
    queue_event(ctx.db, "finding_lost", {
        "finding_id": f.id, "instance_id": f.instance_id, "title": _title(ctx.db, f),
        "type": f.type, "servers": servers, "actions_taken": taken,
    })
    ctx.db.commit()


def handle_losses(ctx: ScanContext) -> None:
    """Apply each instance's loss handling to the findings lost in this scan."""
    if ctx.dry_run:
        return
    for finding_id in ctx.result.lost_ids:
        taken: list[str] = []
        f = ctx.db.get(Finding, finding_id)
        if f is None:
            continue
        try:
            inst = find_instance(ctx.config, f.instance_id)
            if inst is not None and ctx.instance_ok.get(inst.id) is True:
                taken = _handle(ctx, f, inst)
        except Exception as e:  # noqa: BLE001 - loss handling must never fail the scan
            ctx.db.rollback()
            log.error("loss handling failed for finding %s: %s", finding_id, redact(str(e)))
        try:
            _notify(ctx, f, taken)
        except Exception as e:  # noqa: BLE001
            ctx.db.rollback()
            log.error("loss notification failed for finding %s: %s", finding_id, redact(str(e)))
