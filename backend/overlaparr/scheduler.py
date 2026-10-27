"""Background scheduler: scans, notification digests and retention."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from overlaparr.config.schema import AppConfig
from overlaparr.config.store import ConfigStore
from overlaparr.cron import next_runs
from overlaparr.db.models import ActionLog, NotificationLog, PendingEvent
from overlaparr.notify.digest import send_digest
from overlaparr.scan.engine import ScanEngine

__all__ = ["AppScheduler", "next_runs", "run_retention"]

log = logging.getLogger(__name__)

HOUSEKEEPING_DAYS = 90
_JOB_OPTS = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 300}


def _env_tz() -> ZoneInfo:
    name = os.environ.get("TZ")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            log.warning("invalid TZ %r, falling back to UTC", name)
    return ZoneInfo("UTC")


def run_retention(db: Session, cfg: AppConfig, now: datetime) -> dict[str, int]:
    counts = {"action_log": 0, "notification_log": 0, "pending_events": 0}
    days = cfg.action_log.retention_days
    if days is not None:
        counts["action_log"] = db.execute(
            delete(ActionLog).where(ActionLog.ts < now - timedelta(days=days))
        ).rowcount
    cutoff = now - timedelta(days=HOUSEKEEPING_DAYS)
    counts["notification_log"] = db.execute(
        delete(NotificationLog).where(NotificationLog.ts < cutoff)).rowcount
    counts["pending_events"] = db.execute(
        delete(PendingEvent).where(PendingEvent.sent.is_(True), PendingEvent.ts < cutoff)
    ).rowcount
    db.commit()
    return counts


class AppScheduler:
    def __init__(
        self,
        store: ConfigStore,
        engine: ScanEngine,
        db_factory: sessionmaker[Session],
        tz: ZoneInfo | None = None,
    ):
        self.store = store
        self.engine = engine
        self.db_factory = db_factory
        self.tz = tz or _env_tz()
        self.scheduler = BackgroundScheduler(timezone=self.tz)
        self._lock = threading.Lock()

    def start(self) -> None:
        self.apply(self.store.current)
        self.store.subscribe(self.apply)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def apply(self, cfg: AppConfig) -> None:
        with self._lock:
            for job in self.scheduler.get_jobs():
                job.remove()
            self._add("scan", self._scan, CronTrigger.from_crontab(cfg.scan.cron, self.tz))
            self._add("retention", self._retention, CronTrigger(hour=3, minute=30, timezone=self.tz))
            for conn in cfg.notifications.connections:
                trigger = self._digest_trigger(conn.digest)
                if conn.enabled and trigger is not None:
                    self._add(f"digest:{conn.id}", self._digest, trigger, args=[conn.id])

    def _add(self, job_id: str, func, trigger, args=None) -> None:
        self.scheduler.add_job(func, trigger, args=args, id=job_id, replace_existing=True,
                               **_JOB_OPTS)

    def _digest_trigger(self, d) -> CronTrigger | None:
        if d.mode == "off":
            return None
        if d.mode == "cron":
            return CronTrigger.from_crontab(d.cron, self.tz)
        hour, minute = (int(x) for x in d.time.split(":"))
        dow = d.weekday if d.mode == "weekly" else None
        return CronTrigger(day_of_week=dow, hour=hour, minute=minute, timezone=self.tz)

    # ----- jobs -------------------------------------------------------

    def _scan(self) -> None:
        self.engine.start("schedule")

    def _digest(self, conn_id: str) -> None:
        cfg = self.store.current
        conn = next((c for c in cfg.notifications.connections if c.id == conn_id), None)
        if conn is None or not conn.enabled:
            return
        with self.db_factory() as db:
            send_digest(db, conn, app_url=cfg.notifications.app_url)

    def _retention(self) -> None:
        from overlaparr.db.models import utcnow

        with self.db_factory() as db:
            counts = run_retention(db, self.store.current, utcnow())
        log.info("retention removed %s", counts)
