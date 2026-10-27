"""Reverse a logged action, recording the reversal as its own ActionLog row."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from overlaparr.actions._ops import FILES_GONE
from overlaparr.actions.executor import REVIEW_ACTIONS, find_instance
from overlaparr.arr.client import ArrError
from overlaparr.arr.radarr import RadarrClient
from overlaparr.arr.sonarr import SonarrClient
from overlaparr.arr.snapshot import make_client
from overlaparr.config.schema import AppConfig
from overlaparr.db.models import ActionLog, Finding, FindingServerIgnore, utcnow
from overlaparr.logging_setup import redact


class UndoError(Exception):
    """The action cannot be undone (nothing is logged)."""


def is_undoable(row: ActionLog) -> bool:
    """Whether ``undo`` would accept this row (not yet undone, and it changed something).

    An error row qualifies only for a monitoring change applied before the failure.
    """
    if not row.undoable or row.undone_at is not None:
        return False
    partial = row.result == "error" and bool((row.prior_state or {}).get("monitored"))
    return row.result == "ok" or partial


def _undo_review(db: Session, orig: ActionLog, finding: Finding, row: ActionLog) -> str:
    if orig.action == "ignore_server":
        db.execute(delete(FindingServerIgnore).where(
            FindingServerIgnore.finding_id == finding.id,
            FindingServerIgnore.server_id == orig.scope.get("server_id"),
        ))
        return "server no longer ignored"
    row.prior_state = {"review_state": finding.review_state}
    finding.review_state = orig.prior_state["review_state"]
    return f"review state restored to '{finding.review_state}'"


def _undo_arr(orig: ActionLog, client: RadarrClient | SonarrClient, is_radarr: bool) -> str:
    prior = orig.prior_state
    if orig.action in ("unmonitor", "delete"):
        if is_radarr:  # not isinstance: a borrowed client is a proxy
            setter = client.set_movies_monitored
        elif orig.action == "unmonitor" and orig.scope.get("scope") == "item":
            setter = client.set_series_monitored
        else:
            setter = client.set_episodes_monitored
        groups: dict[bool, list[int]] = {}
        for item_id, value in prior.get("monitored", {}).items():
            groups.setdefault(bool(value), []).append(int(item_id))
        for value, ids in sorted(groups.items()):
            setter(ids, value)
        return "monitoring restored" + (f"; {FILES_GONE}" if orig.action == "delete" else "")
    if orig.action == "exclude":
        client.remove_exclusion(prior["exclusion_id"])
        return "exclusion removed"
    if orig.action == "tag":
        client.edit_tags([orig.arr_id], list(prior["tags_added"]), "remove")
        return "tag removed"
    raise UndoError(f"'{orig.action}' cannot be undone")


def undo(
    db: Session,
    cfg: AppConfig,
    log_id: int,
    actor: str = "ui",
    client_for: Callable = make_client,
) -> ActionLog:
    """Undo a logged action; returns the new committed row (``result`` ok or error).

    Raises ``UndoError`` (without logging) when the row is not undoable or its
    instance is no longer configured.
    """
    orig = db.get(ActionLog, log_id)
    if orig is None:
        raise UndoError(f"Action {log_id} not found")
    if not is_undoable(orig):
        raise UndoError("This action cannot be undone")
    finding = db.get(Finding, orig.finding_id) if orig.finding_id is not None else None
    row = ActionLog(
        actor=actor,
        instance_id=orig.instance_id,
        instance_name=orig.instance_name,
        finding_id=orig.finding_id,
        arr_id=orig.arr_id,
        item_title=orig.item_title,
        action=orig.action,
        scope=dict(orig.scope),
        prior_state={},
        undoable=False,
        undo_of_id=orig.id,
    )
    if orig.action in REVIEW_ACTIONS:
        if finding is None:
            raise UndoError("Finding no longer exists")
        row.result, row.message = "ok", _undo_review(db, orig, finding, row)
    else:
        inst = find_instance(cfg, orig.instance_id)
        if inst is None:
            raise UndoError(f"Instance '{orig.instance_name}' no longer configured")
        client = client_for(inst, dry_run=False)
        try:
            row.result, row.message = "ok", _undo_arr(orig, client, inst.type == "radarr")
        except ArrError as e:
            row.result, row.message = "error", redact(str(e))
        finally:
            client.close()
    if row.result == "ok":
        orig.undone_at = utcnow()
        # Hold only when a person reverses automation, not when automation undoes itself.
        if orig.actor == "auto" and actor != "auto" and finding is not None:
            finding.automation_hold = True
    db.add(row)
    db.commit()
    return row
