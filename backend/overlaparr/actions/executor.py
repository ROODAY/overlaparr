"""Carry out one action on a finding and record it as exactly one ActionLog row."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.actions import _ops
from overlaparr.actions._ops import ActionError, OpContext
from overlaparr.arr.client import ArrError
from overlaparr.arr.snapshot import make_client
from overlaparr.config.schema import AppConfig, InstanceConfig, Rule
from overlaparr.db.models import ActionLog, Finding, FindingServerIgnore, TrackedEpisode, TrackedItem
from overlaparr.logging_setup import redact

__all__ = ["ActionError", "ActionRequest", "execute", "find_instance", "validate_request"]

ActionName = Literal[
    "unmonitor", "delete", "exclude", "tag", "ignore", "ignore_server", "mark_reviewed",
    "unignore", "mark_new",
]  # fmt: skip

REVIEW_TARGET = {"ignore": "ignored", "unignore": "new", "mark_reviewed": "reviewed",
                 "mark_new": "new"}
REVIEW_ACTIONS = frozenset(REVIEW_TARGET) | {"ignore_server"}
EPISODE_SCOPE_ACTIONS = frozenset({"unmonitor", "delete"})


@dataclass
class ActionRequest:
    finding_id: int
    action: ActionName
    scope: Literal["item", "episodes"] = "item"
    episode_ids: list[int] = field(default_factory=list)
    delete_mode: Literal["files", "remove_item"] = "files"
    add_exclusion: bool = False
    server_id: str | None = None


def find_instance(cfg: AppConfig, instance_id: str) -> InstanceConfig | None:
    return next((i for i in cfg.instances if i.id == instance_id), None)


def _scope(req: ActionRequest) -> dict:
    scope: dict = {"scope": req.scope}
    if req.scope == "episodes":
        scope["episode_ids"] = list(req.episode_ids)
    if req.action == "delete":
        scope["delete_mode"] = req.delete_mode
        scope["add_exclusion"] = req.add_exclusion
    if req.server_id is not None:
        scope["server_id"] = req.server_id
    return scope


def _review(db: Session, finding: Finding, req: ActionRequest, row: ActionLog,
            dry_run: bool) -> tuple[bool, str | None]:
    if req.action == "ignore_server":
        if not req.server_id:
            raise ActionError("ignore_server requires a server_id")
        existing = db.scalar(select(FindingServerIgnore).where(
            FindingServerIgnore.finding_id == finding.id,
            FindingServerIgnore.server_id == req.server_id,
        ))
        if existing is not None:
            return False, "server already ignored"
        if not dry_run:
            db.add(FindingServerIgnore(finding_id=finding.id, server_id=req.server_id))
        return True, None
    row.prior_state = {"review_state": finding.review_state}
    if not dry_run:
        finding.review_state = REVIEW_TARGET[req.action]
    return True, None


def _validate_arr(db: Session, finding: Finding, inst: InstanceConfig | None,
                  req: ActionRequest) -> None:
    if inst is None:
        raise ActionError("Instance no longer configured")
    if req.action == "delete" and req.delete_mode == "remove_item" and req.scope != "item":
        raise ActionError("remove_item is only valid with scope 'item'")
    if req.scope != "episodes":
        return
    if req.action not in EPISODE_SCOPE_ACTIONS:
        raise ActionError(f"'{req.action}' only supports scope 'item'")
    if finding.kind != "series":
        raise ActionError("Episode scope requires a series")
    if not req.episode_ids:
        raise ActionError("No episodes selected")
    known = set(db.scalars(select(TrackedEpisode.episode_arr_id).where(
        TrackedEpisode.instance_id == finding.instance_id,
        TrackedEpisode.series_arr_id == finding.arr_id,
    )))
    unknown = sorted(set(req.episode_ids) - known)
    if unknown:
        raise ActionError(f"Episodes {unknown} do not belong to this series")


def validate_request(db: Session, cfg: AppConfig, finding: Finding, req: ActionRequest) -> None:
    """Raise ``ActionError`` when ``execute`` would reject ``req`` before any *arr call."""
    if req.action in REVIEW_ACTIONS:
        if req.action == "ignore_server" and not req.server_id:
            raise ActionError("ignore_server requires a server_id")
        return
    _validate_arr(db, finding, find_instance(cfg, finding.instance_id), req)


def _arr(db: Session, cfg: AppConfig, finding: Finding, inst: InstanceConfig | None,
         req: ActionRequest, row: ActionLog, dry_run: bool,
         client_for: Callable) -> tuple[bool, str | None]:
    _validate_arr(db, finding, inst, req)
    client = client_for(inst, dry_run=dry_run)
    ctx = OpContext(cfg=cfg, finding=finding, client=client, scope=req.scope,
                    episode_ids=list(req.episode_ids), delete_mode=req.delete_mode,
                    add_exclusion=req.add_exclusion, dry_run=dry_run)
    try:
        if req.action == "delete":
            op = _ops.remove_item if req.delete_mode == "remove_item" else _ops.delete_files
        else:
            op = _ops.ARR_OPS[req.action]
        outcome = op(ctx)
    finally:
        row.prior_state = dict(ctx.prior)  # partial state is kept even on failure
        client.close()
    if not dry_run and outcome.changed:  # a no-op leaves the finding untouched
        finding.review_state = "actioned"
        if req.action == "delete" and req.delete_mode == "remove_item":
            finding.lifecycle = "removed"
    return outcome.undoable, outcome.message


def execute(
    db: Session,
    cfg: AppConfig,
    req: ActionRequest,
    actor: Literal["ui", "auto"],
    rule: Rule | None = None,
    scan_id: int | None = None,
    dry_run: bool = False,
    client_for: Callable = make_client,
) -> ActionLog:
    """Run the action and return its committed log row; callers check ``row.result``.

    *arr and validation errors never raise: they produce a ``result="error"`` row.
    Only an unknown finding raises ``ActionError`` (there is nothing to log against).
    """
    finding = db.get(Finding, req.finding_id)
    if finding is None:
        raise ActionError(f"Finding {req.finding_id} not found")
    inst = find_instance(cfg, finding.instance_id)
    item = db.scalar(select(TrackedItem).where(
        TrackedItem.instance_id == finding.instance_id, TrackedItem.arr_id == finding.arr_id
    ))
    row = ActionLog(
        actor=actor,
        rule_id=rule.id if rule else None,
        rule_name=rule.name if rule else None,
        scan_run_id=scan_id,
        instance_id=finding.instance_id,
        instance_name=inst.name if inst else finding.instance_id,
        finding_id=finding.id,
        arr_id=finding.arr_id,
        item_title=item.title if item else f"{finding.kind} {finding.arr_id}",
        action=req.action,
        scope=_scope(req),
        prior_state={},
        undoable=False,
    )
    try:
        if req.action in REVIEW_ACTIONS:
            undoable, message = _review(db, finding, req, row, dry_run)
        else:
            undoable, message = _arr(db, cfg, finding, inst, req, row, dry_run, client_for)
    except (ArrError, ActionError) as e:
        db.rollback()  # discard any half-applied DB change; the row is still transient
        row.result, row.message = "error", redact(str(e))
        # A monitoring change applied before the failure stays undoable.
        row.undoable = not dry_run and bool(row.prior_state.get("monitored"))
    else:
        row.result = "dry_run" if dry_run else "ok"
        row.undoable = undoable and not dry_run
        row.message = message
    db.add(row)
    db.commit()
    return row
