"""Activity: the action log, filtered and paginated, and undo of a logged action."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.actions.undo import UndoError, is_undoable, undo
from overlaparr.api.deps import get_db, get_store
from overlaparr.arr.snapshot import make_client
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import ActionLog

router = APIRouter(prefix="/activity", tags=["activity"])


def log_row(row: ActionLog) -> dict:
    return {
        "id": row.id,
        "ts": row.ts,
        "actor": row.actor,
        "rule_id": row.rule_id,
        "rule_name": row.rule_name,
        "scan_run_id": row.scan_run_id,
        "instance_id": row.instance_id,
        "instance_name": row.instance_name,
        "finding_id": row.finding_id,
        "arr_id": row.arr_id,
        "item_title": row.item_title,
        "action": row.action,
        "scope": row.scope,
        "prior_state": row.prior_state,
        "result": row.result,
        "message": row.message,
        "undoable": row.undoable,
        "undone_at": row.undone_at,
        "undo_of_id": row.undo_of_id,
        "can_undo": is_undoable(row),
    }


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@router.get("")
def list_activity(
    actor: str | None = None,
    action: str | None = None,
    instance: str | None = None,
    rule: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    conds = []
    if actor:
        conds.append(ActionLog.actor == actor)
    if action:
        conds.append(ActionLog.action == action)
    if instance:
        conds.append(ActionLog.instance_id == instance)
    if rule:
        conds.append(ActionLog.rule_id == rule)
    if since is not None:
        conds.append(ActionLog.ts >= _utc(since))
    if until is not None:
        conds.append(ActionLog.ts <= _utc(until))
    total = db.scalar(select(func.count()).select_from(ActionLog).where(*conds))
    rows = db.scalars(
        select(ActionLog).where(*conds).order_by(ActionLog.ts.desc(), ActionLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size))
    return {"items": [log_row(r) for r in rows], "total": total}


@router.post("/{log_id}/undo")
def undo_action(log_id: int, request: Request, db: Session = Depends(get_db),
                store: ConfigStore = Depends(get_store)):
    if db.get(ActionLog, log_id) is None:
        raise HTTPException(status_code=404, detail="Unknown action")
    client_for = getattr(request.app.state, "client_factory", None) or make_client
    try:
        row = undo(db, store.current, log_id, actor="ui", client_for=client_for)
    except UndoError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return log_row(row)
