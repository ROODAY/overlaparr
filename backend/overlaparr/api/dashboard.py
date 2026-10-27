"""Dashboard: finding counts, reclaimable space, scan timing and source health."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.api.deps import config_error, get_db, get_store
from overlaparr.api.scan import run_dict
from overlaparr.config.schema import AppConfig
from overlaparr.config.store import ConfigStore
from overlaparr.cron import next_runs
from overlaparr.db.models import Finding, ScanRun, Server
from overlaparr.stats import reclaimable_bytes

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TYPES = ("wanted_elsewhere", "on_disk")
COVERAGES = ("full", "partial")
STATES = ("new", "reviewed", "ignored", "actioned")


def _counts(db: Session, column, keys: tuple[str, ...]) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).where(Finding.lifecycle == "active")
                      .group_by(column))
    found = {key: n for key, n in rows}
    return {k: found.get(k, 0) for k in keys}


def _next_scan(request: Request, cfg: AppConfig) -> datetime | None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        job = scheduler.scheduler.get_job("scan")
        when = getattr(job, "next_run_time", None) if job is not None else None
        if isinstance(when, datetime):  # pending (not yet started) jobs have none
            return when
    try:
        runs = next_runs(cfg.scan.cron, 1, getattr(scheduler, "tz", None))
    except ValueError:
        return None
    return runs[0] if runs else None


def _servers(db: Session, cfg: AppConfig) -> list[dict]:
    labels = {o.id: o.label for o in cfg.servers if o.label}
    rows = [
        {"id": s.machine_id, "label": labels.get(s.machine_id) or s.name,
         "reachability": s.reachability, "last_indexed_at": s.last_indexed_at}
        for s in db.scalars(select(Server))
        if s.machine_id != cfg.plex.home_server
    ]
    return sorted(rows, key=lambda r: r["label"].lower())


def _instances(db: Session, cfg: AppConfig) -> list[dict]:
    """Configured instances with their health from the newest scan that read instances.

    A scan that failed early (e.g. Plex unreachable) records no instance results, so
    the newest run alone would hide the last known health.
    """
    ok: dict = {}
    for summary in db.scalars(select(ScanRun.summary).order_by(ScanRun.id.desc())):
        if isinstance(summary, dict) and isinstance(summary.get("instances"), dict):
            ok = summary["instances"]
            break
    return [{"id": i.id, "name": i.name, "ok": ok.get(i.id)} for i in cfg.instances]


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db),
              store: ConfigStore = Depends(get_store)):
    cfg = store.current
    last = db.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))
    return {
        "counts": {
            "by_type": _counts(db, Finding.type, TYPES),
            "by_coverage": _counts(db, Finding.coverage, COVERAGES),
            "by_state": _counts(db, Finding.review_state, STATES),
        },
        "reclaimable_bytes": reclaimable_bytes(db),
        "lost": db.scalar(select(func.count()).select_from(Finding)
                          .where(Finding.lifecycle == "lost")),
        "last_scan": run_dict(last) if last is not None else None,
        "next_scan": _next_scan(request, cfg),
        "servers": _servers(db, cfg),
        "instances": _instances(db, cfg),
        "config_error": config_error(store),
    }
