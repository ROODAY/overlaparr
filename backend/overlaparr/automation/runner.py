"""Post-scan automation: put manually changed findings on hold, then run rules in order.

Each rule keeps a per-finding wait counter (RuleState) and acts only after the
finding has matched for ``wait_scans`` consecutive scans with stable coverage.
Everything here reads the DB snapshot the scan just persisted; the only *arr calls
are the ones ``execute`` makes for an action.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.actions.executor import EPISODE_SCOPE_ACTIONS, ActionRequest, execute
from overlaparr.arr.snapshot import make_client
from overlaparr.automation.rules import RuleMatch, evaluate
from overlaparr.config.schema import AppConfig, InstanceConfig, Rule
from overlaparr.db.models import (
    ActionLog,
    Finding,
    FindingCopy,
    FindingServerIgnore,
    RuleState,
    TrackedEpisode,
    TrackedItem,
)
from overlaparr.logging_setup import redact
from overlaparr.scan.engine import ScanContext

log = logging.getLogger(__name__)

_LIVE = {"online", "degraded"}


class _Borrowed:
    """A scan-owned client lent to ``execute``, which closes clients when done."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def close(self) -> None:  # the scan engine closes the real client
        pass


def _client_for(ctx: ScanContext) -> Callable:
    def get(inst: InstanceConfig, dry_run: bool = False) -> Any:
        if dry_run or inst.id not in ctx.clients:
            return make_client(inst, dry_run=dry_run)
        return _Borrowed(ctx.clients[inst.id])

    return get


# ----- hold detection ---------------------------------------------------------


def _remonitored(db: Session, f: Finding, row: ActionLog) -> bool:
    ids = [int(k) for k, was in (row.prior_state.get("monitored") or {}).items() if was is True]
    if not ids:
        return False
    if f.kind == "movie":
        item = db.scalar(select(TrackedItem).where(
            TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id
        ))
        return item is not None and f.arr_id in ids and item.monitored
    if row.scope.get("scope") != "episodes":
        return False  # series-level monitoring is never changed by automation
    return db.scalar(select(TrackedEpisode.id).where(
        TrackedEpisode.instance_id == f.instance_id,
        TrackedEpisode.episode_arr_id.in_(ids),
        TrackedEpisode.monitored.is_(True),
    ).limit(1)) is not None


def detect_manual_changes(ctx: ScanContext) -> list[int]:
    """Hold findings whose latest automatic unmonitor/delete was re-monitored by the user."""
    db = ctx.db
    rows = db.scalars(select(ActionLog).where(
        ActionLog.actor == "auto",
        ActionLog.result == "ok",
        ActionLog.undone_at.is_(None),
        ActionLog.action.in_(("unmonitor", "delete")),
        ActionLog.finding_id.is_not(None),
    ).order_by(ActionLog.id.desc()))
    latest: dict[int, ActionLog] = {}
    for row in rows:
        # A later no-op row records nothing, so the last row that changed monitoring counts.
        if row.finding_id not in latest and (row.prior_state.get("monitored") or {}):
            latest[row.finding_id] = row
    held: list[int] = []
    for finding_id, row in sorted(latest.items()):
        f = db.get(Finding, finding_id)
        if f is None or f.automation_hold or ctx.instance_ok.get(f.instance_id) is not True:
            continue
        if _remonitored(db, f, row):
            f.automation_hold = True
            held.append(f.id)
    if held:
        db.commit()
        log.info("scan %s: automation hold set on findings %s (re-monitored manually)",
                 ctx.scan_id, held)
    return held


# ----- rules ------------------------------------------------------------------


def _coverage_hash(m: RuleMatch) -> str:
    key = json.dumps([sorted(m.episode_ids), sorted(m.servers)])
    return hashlib.sha1(key.encode()).hexdigest()


def _count(ctx: ScanContext, rule: Rule, m: RuleMatch, state: RuleState | None) -> bool:
    """Advance the wait counter; True when the rule may act on this finding now.

    An unreachable server pauses the counter (no change, no action this scan).
    """
    if any(ctx.server_status.get(s) not in _LIVE for s in m.servers):
        return False
    h = _coverage_hash(m)
    if state is None:
        state = RuleState(rule_id=rule.id, finding_id=m.finding_id, consecutive_scans=1,
                          coverage_hash=h, last_counted_scan_id=ctx.scan_id)
        ctx.db.add(state)
    elif state.coverage_hash != h:
        state.consecutive_scans, state.coverage_hash = 1, h
        state.last_counted_scan_id = ctx.scan_id
    elif state.last_counted_scan_id != ctx.scan_id:
        state.consecutive_scans += 1
        state.last_counted_scan_id = ctx.scan_id
    return state.consecutive_scans >= rule.wait_scans


def _guard_series(req: ActionRequest) -> None:
    """Automation must never touch series-level monitoring or remove a series."""
    if req.action in EPISODE_SCOPE_ACTIONS and req.scope != "episodes":
        raise AssertionError(f"series '{req.action}' must be episode-scoped")
    if req.delete_mode == "remove_item":
        raise AssertionError("automation never removes a series")


def _request(rule: Rule, f: Finding, m: RuleMatch) -> ActionRequest:
    if f.kind != "series":
        return ActionRequest(f.id, rule.action, delete_mode=rule.delete_mode,
                             add_exclusion=rule.add_exclusion)
    if rule.action in EPISODE_SCOPE_ACTIONS:
        req = ActionRequest(f.id, rule.action, scope="episodes", episode_ids=list(m.episode_ids),
                            delete_mode="files", add_exclusion=False)
    else:
        req = ActionRequest(f.id, rule.action)
    _guard_series(req)
    return req


def _targets(db: Session, f: Finding, req: ActionRequest) -> list[TrackedItem | TrackedEpisode]:
    if req.scope == "episodes":
        return list(db.scalars(select(TrackedEpisode).where(
            TrackedEpisode.instance_id == f.instance_id,
            TrackedEpisode.episode_arr_id.in_(req.episode_ids),
        )))
    item = db.scalar(select(TrackedItem).where(
        TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id
    ))
    return [item] if item is not None else []


def _dupe_tag_id(db: Session, instance_id: str) -> int | None:
    """The dupe tag's id as learned from an earlier tag action (no *arr call)."""
    for row in db.scalars(select(ActionLog).where(
        ActionLog.instance_id == instance_id, ActionLog.action == "tag",
        ActionLog.result == "ok",
    ).order_by(ActionLog.id.desc())):
        added = row.prior_state.get("tags_added") or []
        if added:
            return added[0]
    return None


def _already_done(db: Session, f: Finding, req: ActionRequest) -> bool:
    """True when the DB snapshot shows the target state already holds (skip, no log)."""
    if req.action == "ignore":
        return f.review_state == "ignored"
    if req.action == "exclude":
        return db.scalar(select(ActionLog.id).where(
            ActionLog.finding_id == f.id, ActionLog.action == "exclude",
            ActionLog.result == "ok", ActionLog.undone_at.is_(None),
        ).limit(1)) is not None
    if req.action == "tag":
        tag_id = _dupe_tag_id(db, f.instance_id)
        targets = _targets(db, f, req)
        return tag_id is not None and bool(targets) and tag_id in (targets[0].tag_ids or [])
    if req.action == "delete" and req.delete_mode == "remove_item":
        return False
    targets = _targets(db, f, req)
    expected = len(req.episode_ids) if req.scope == "episodes" else 1
    if len(targets) != expected:
        return False  # snapshot incomplete: let execute check the live state
    if req.action == "unmonitor":
        return all(not t.monitored for t in targets)
    return all(not t.monitored and not t.has_file for t in targets)  # delete files


def _load(db: Session, findings: list[Finding]) -> tuple[dict, dict]:
    ids = [f.id for f in findings]
    copies: dict[int, list[FindingCopy]] = defaultdict(list)
    for c in db.scalars(select(FindingCopy).where(FindingCopy.finding_id.in_(ids))):
        copies[c.finding_id].append(c)
    ignores: dict[int, set[str]] = defaultdict(set)
    for i in db.scalars(select(FindingServerIgnore).where(FindingServerIgnore.finding_id.in_(ids))):
        ignores[i.finding_id].add(i.server_id)
    return copies, ignores


def _run_rule(ctx: ScanContext, rule: Rule, dry_run: bool, written: list[ActionLog]) -> None:
    db = ctx.db
    if rule.action == "delete" and not rule.delete_confirmed:
        log.warning("rule %s skipped: delete rules require delete_confirmed", rule.id)
        return
    ok = [iid for iid, good in ctx.instance_ok.items() if good is True]
    findings = list(db.scalars(
        select(Finding).where(Finding.instance_id.in_(ok)).order_by(Finding.id)
    ))
    copies, ignores = _load(db, findings)
    states = {s.finding_id: s for s in db.scalars(
        select(RuleState).where(RuleState.rule_id == rule.id)
    )}
    client_for = _client_for(ctx)
    for f in findings:
        m = evaluate(rule, f, copies[f.id], ignores[f.id], list(f.relevant_episode_ids))
        state = states.get(f.id)
        if m is None:
            if state is not None:
                db.delete(state)
                db.commit()
            continue
        ready = _count(ctx, rule, m, state)
        db.commit()  # keep the counter even if the action below fails and rolls back
        if not ready:
            continue
        req = _request(rule, f, m)
        if _already_done(db, f, req):
            continue
        written.append(execute(db, ctx.config, req, "auto", rule=rule, scan_id=ctx.scan_id,
                               dry_run=dry_run, client_for=client_for))


def _consume_dry_run_flag(ctx: ScanContext, rule_id: str) -> None:
    def mutate(cfg: AppConfig) -> None:
        for r in cfg.rules:
            if r.id == rule_id:
                r.dry_run_next = False

    try:
        ctx.store.update(mutate)
    except Exception as e:  # noqa: BLE001 - the dry run itself already happened
        log.error("rule %s: could not clear dry_run_next: %s", rule_id, redact(str(e)))


def run_rules(ctx: ScanContext) -> list[ActionLog]:
    """Scan post-hook: hold detection, then every active rule in config order.

    Returns the ActionLog rows written (including dry-run rows). Never raises.
    """
    try:
        detect_manual_changes(ctx)
    except Exception as e:  # noqa: BLE001
        ctx.db.rollback()
        # Acting without knowing about manual changes could undo the user's own edits.
        log.error("scan %s: hold detection failed, rules skipped: %s", ctx.scan_id,
                  redact(str(e)))
        return
    written: list[ActionLog] = []
    for rule in ctx.config.rules:
        if not (rule.enabled or rule.dry_run_next):
            continue
        dry_run = ctx.dry_run or rule.dry_run_next
        try:
            _run_rule(ctx, rule, dry_run, written)  # rows land in written even if it fails
        except Exception as e:  # noqa: BLE001 - one broken rule must not stop the others
            ctx.db.rollback()
            log.error("scan %s: rule %s failed: %s", ctx.scan_id, rule.id, redact(str(e)))
            continue
        if rule.dry_run_next and not ctx.dry_run:
            _consume_dry_run_flag(ctx, rule.id)
    return written
