"""Automation rules: CRUD, ordering, preview, dry-run and the per-action warning copy."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.api.deps import get_db, get_store, update_config, validation_detail
from overlaparr.automation.rules import preview
from overlaparr.config.schema import AppConfig, Rule, new_id
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import ActionLog, Finding, TrackedItem

router = APIRouter(prefix="/rules", tags=["rules"])

DELETE_UNCONFIRMED = "Delete rules require confirmation"

WARNINGS = {
    "unmonitor": "Scheduled scans will unmonitor matching items in Sonarr/Radarr. "
                 "You can undo this from the Activity log.",
    "delete": "Scheduled scans will permanently delete files from disk for every matching "
              "item. Overlaparr cannot undo this. Matches against servers that are offline "
              "are never acted on.",
    "exclude": "Scheduled scans will add matching items to the import list exclusions so "
               "they won't be re-added.",
    "tag": "Scheduled scans will add Overlaparr tags to matching items.",
    "ignore": "Scheduled scans will mark matching findings as ignored.",
}

class ReorderIn(BaseModel):
    ids: list[str]


class PreviewIn(BaseModel):
    rule: dict[str, Any]


READ_ONLY = {"id", "last_run", "last_affected"}  # server-owned; GET output can be sent back


def _build(rule_id: str, data: dict[str, Any]) -> Rule:
    data = {k: v for k, v in data.items() if k not in READ_ONLY}
    try:
        return Rule(id=rule_id, **data)
    except ValidationError as e:
        try:  # was the missing delete confirmation the only problem?
            Rule(id=rule_id, **{**data, "delete_confirmed": True})
        except ValidationError:
            pass
        else:
            raise HTTPException(status_code=422, detail=DELETE_UNCONFIRMED) from None
        raise HTTPException(status_code=422, detail=validation_detail(e)) from None


def _index(cfg: AppConfig, rule_id: str) -> int:
    idx = next((n for n, r in enumerate(cfg.rules) if r.id == rule_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Unknown rule")
    return idx


def _out(cfg: AppConfig, rule_id: str) -> dict:
    return cfg.rules[_index(cfg, rule_id)].model_dump()


def _last_runs(db: Session, rule_ids: list[str]) -> dict[str, tuple[Any, int]]:
    """rule id -> (ts of its newest ActionLog row, rows in that row's scan run)."""
    if not rule_ids:
        return {}
    rank = func.row_number().over(partition_by=ActionLog.rule_id,
                                  order_by=(ActionLog.ts.desc(), ActionLog.id.desc()))
    ranked = select(ActionLog.rule_id, ActionLog.ts, ActionLog.scan_run_id,
                    rank.label("n")).where(ActionLog.rule_id.in_(rule_ids)).subquery()
    newest = {rid: (ts, run) for rid, ts, run in db.execute(
        select(ranked.c.rule_id, ranked.c.ts, ranked.c.scan_run_id).where(ranked.c.n == 1))}
    # GROUP BY puts NULL scan runs together, as "scan_run_id IS NULL" would.
    counts = {(rid, run): n for rid, run, n in db.execute(
        select(ActionLog.rule_id, ActionLog.scan_run_id, func.count())
        .where(ActionLog.rule_id.in_(list(newest)))
        .group_by(ActionLog.rule_id, ActionLog.scan_run_id))}
    return {rid: (ts, counts.get((rid, run), 0)) for rid, (ts, run) in newest.items()}


@router.get("")
def list_rules(db: Session = Depends(get_db), store: ConfigStore = Depends(get_store)):
    rules = store.current.rules
    last = _last_runs(db, [r.id for r in rules])
    out = []
    for rule in rules:
        last_run, last_affected = last.get(rule.id, (None, 0))
        out.append({**rule.model_dump(), "last_run": last_run, "last_affected": last_affected})
    return out


@router.post("", status_code=201)
def create_rule(body: dict[str, Any] = Body(...), store: ConfigStore = Depends(get_store)):
    rule = _build(new_id(), body)
    return _out(update_config(store, lambda c: c.rules.append(rule)), rule.id)


@router.get("/warning")
def rule_warning(action: Literal["unmonitor", "delete", "exclude", "tag", "ignore"]):
    return {"text": WARNINGS[action]}


@router.post("/reorder")
def reorder_rules(body: ReorderIn, store: ConfigStore = Depends(get_store)):
    def mutate(c: AppConfig) -> None:
        by_id = {r.id: r for r in c.rules}
        if len(body.ids) != len(set(body.ids)) or set(body.ids) != set(by_id):
            raise HTTPException(status_code=422,
                                detail="ids must list every existing rule exactly once")
        c.rules = [by_id[i] for i in body.ids]

    return [r.model_dump() for r in update_config(store, mutate).rules]


@router.post("/preview")
def preview_rule(body: PreviewIn, db: Session = Depends(get_db)):
    # Enabled/confirmed state does not affect matching; preview any draft rule.
    data = {**body.rule, "enabled": False}
    rule = _build(str(body.rule.get("id") or "preview"), data)
    matches = preview(db, rule)
    findings = {f.id: f for f in db.scalars(
        select(Finding).where(Finding.id.in_([m.finding_id for m in matches])))}
    keys = {(f.instance_id, f.arr_id) for f in findings.values()}
    titles = {(t.instance_id, t.arr_id): t.title for t in db.scalars(select(TrackedItem).where(
        TrackedItem.instance_id.in_({k[0] for k in keys}),
        TrackedItem.arr_id.in_({k[1] for k in keys})))}
    items = []
    for m in matches:
        f = findings[m.finding_id]
        items.append({"finding_id": m.finding_id, "title": titles.get((f.instance_id, f.arr_id)),
                      "episodes": len(m.episode_ids)})
    return {"count": len(items), "items": items}


@router.put("/{rule_id}")
def update_rule(rule_id: str, body: dict[str, Any] = Body(...),
                store: ConfigStore = Depends(get_store)):
    rule = _build(rule_id, body)

    def mutate(c: AppConfig) -> None:
        c.rules[_index(c, rule_id)] = rule

    return _out(update_config(store, mutate), rule_id)


@router.patch("/{rule_id}")
def patch_rule(rule_id: str, body: dict[str, Any] = Body(...),
               store: ConfigStore = Depends(get_store)):
    def mutate(c: AppConfig) -> None:
        idx = _index(c, rule_id)
        existing = c.rules[idx].model_dump()
        changes = {k: v for k, v in body.items() if k not in READ_ONLY}
        if ("action" in changes and changes["action"] != existing["action"]
                and "delete_confirmed" not in changes):
            changes["delete_confirmed"] = False  # confirmation was for the old action
        c.rules[idx] = _build(rule_id, {**existing, **changes})

    return _out(update_config(store, mutate), rule_id)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: str, store: ConfigStore = Depends(get_store)):
    def mutate(c: AppConfig) -> None:
        del c.rules[_index(c, rule_id)]

    update_config(store, mutate)
    return Response(status_code=204)


@router.post("/{rule_id}/dry-run")
def dry_run_rule(rule_id: str, store: ConfigStore = Depends(get_store)):
    def mutate(c: AppConfig) -> None:
        c.rules[_index(c, rule_id)].dry_run_next = True

    return _out(update_config(store, mutate), rule_id)
