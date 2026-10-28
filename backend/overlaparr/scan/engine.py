"""Scan engine: one scan at a time, wiring Plex indexing, *arr reads, matching and reconcile."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from overlaparr.arr.client import ArrClient, ArrError
from overlaparr.arr.snapshot import make_client, persist_snapshot, read_instance
from overlaparr.config.schema import AppConfig, InstanceConfig
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import Finding, IndexItem, ScanRun, Server, TrackedItem, utcnow
from overlaparr.domain.types import ComputedFinding
from overlaparr.logging_setup import redact
from overlaparr.matching.movies import MovieIndex, match_movies
from overlaparr.matching.tv import ShowIndex
from overlaparr.plex.gateway import PlexConnection, PlexGateway
from overlaparr.plex.indexer import connect_server, index_servers, mark_stale, refresh_servers
from overlaparr.scan.pipeline_tv import match_instance_series
from overlaparr.scan.progress import ProgressBus
from overlaparr.scan.reconcile import ReconcileResult, keep_keys, reconcile

log = logging.getLogger(__name__)

Trigger = Literal["schedule", "manual"]
NOT_CONFIGURED = "Plex not configured"
_LIVE = {"online", "degraded"}


class ScanAlreadyRunning(Exception):
    """A scan is already in progress."""


class _ScanFailed(Exception):
    """Expected failure whose message is safe to show as-is."""


@dataclass
class ScanContext:
    scan_id: int
    db: Session
    config: AppConfig
    server_status: dict[str, str]
    instance_ok: dict[str, bool]
    result: ReconcileResult
    clients: dict[str, ArrClient]
    store: ConfigStore
    dry_run: bool = False


PostHook = Callable[[ScanContext], None]


class ScanEngine:
    def __init__(
        self,
        store: ConfigStore,
        db_factory: sessionmaker[Session],
        gateway_factory: Callable[[str], PlexGateway],
        client_factory: Callable[[InstanceConfig], ArrClient] = make_client,
        bus: ProgressBus | None = None,
    ) -> None:
        self.store = store
        self.db_factory = db_factory
        self.gateway_factory = gateway_factory
        self.client_factory = client_factory
        self.bus = bus or ProgressBus()
        self._lock = threading.Lock()
        self._hooks: list[PostHook] = []

    @property
    def running(self) -> bool:
        """True while a scan is in progress."""
        return self._lock.locked()

    def register_post_hook(self, fn: PostHook) -> None:
        self._hooks.append(fn)

    def start(self, trigger: Trigger) -> int | None:
        """Start a scan in a daemon thread; None when one is already running."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            scan_id, started = self._create_run(trigger)
            threading.Thread(
                target=self._run_and_release, args=(scan_id, started), name="scan", daemon=True
            ).start()
        except BaseException:
            self._lock.release()
            raise
        return scan_id

    def run_sync(self, trigger: Trigger) -> int:
        """Run a whole scan in the calling thread; raises ScanAlreadyRunning if busy."""
        if not self._lock.acquire(blocking=False):
            raise ScanAlreadyRunning()
        try:
            scan_id, started = self._create_run(trigger)
        except BaseException:
            self._lock.release()
            raise
        self._run_and_release(scan_id, started)
        return scan_id

    # ----- run lifecycle ----------------------------------------------

    def _create_run(self, trigger: Trigger) -> tuple[int, datetime]:
        started = utcnow()
        with self.db_factory() as db:
            run = ScanRun(started_at=started, status="running", trigger=trigger, summary={})
            db.add(run)
            db.commit()
            return run.id, started

    def _run_and_release(self, scan_id: int, started: datetime) -> None:
        try:
            self._run(scan_id, started)
        finally:
            self._lock.release()

    def _progress(self, scan_id: int, phase: str, detail: str | None = None,
                  done: int = 0, total: int = 0) -> None:
        self.bus.publish({"scan_id": scan_id, "phase": phase, "detail": detail, "done": done,
                          "total": total, "running": True, "status": "running", "error": None})

    def _run(self, scan_id: int, started: datetime) -> None:
        status, error, summary = "ok", None, {}
        try:
            summary = self._execute(scan_id, started)
        except _ScanFailed as e:
            status, error = "failed", str(e)
            log.warning("scan %s failed: %s", scan_id, error)
        except Exception as e:  # noqa: BLE001 - any failure ends the run, never the app
            status, error = "failed", redact(str(e)) or type(e).__name__
            log.error("scan %s failed: %s", scan_id, error)
        try:
            with self.db_factory() as db:
                run = db.get(ScanRun, scan_id)
                run.status, run.error, run.summary = status, error, summary
                run.finished_at = utcnow()
                db.commit()
        finally:
            self.bus.publish({"scan_id": scan_id, "phase": "done", "detail": None, "done": 0,
                              "total": 0, "running": False, "status": status, "error": error})

    # ----- pipeline ---------------------------------------------------

    def _execute(self, scan_id: int, started: datetime) -> dict[str, Any]:
        cfg = self.store.current
        if not cfg.plex.token or not cfg.plex.home_server:
            raise _ScanFailed(NOT_CONFIGURED)
        clients: dict[str, ArrClient] = {}
        with self.db_factory() as db:
            try:
                return self._pipeline(db, cfg, scan_id, started, clients)
            finally:
                for client in clients.values():
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        log.debug("closing *arr client failed", exc_info=True)

    def _pipeline(self, db: Session, cfg: AppConfig, scan_id: int, started: datetime,
                  clients: dict[str, ArrClient]) -> dict[str, Any]:
        home = cfg.plex.home_server
        gateway = self.gateway_factory(cfg.plex.token)

        # Servers: discover, index the enabled others, then age unreachable ones.
        self._progress(scan_id, "servers")
        targets = refresh_servers(db, gateway, home, cfg.servers)
        self._progress(scan_id, "indexing", total=len(targets))
        index_servers(
            self.db_factory, gateway, targets, cfg.scan.server_timeout_seconds,
            cfg.scan.server_index_budget_seconds, cfg.scan.page_size,
            lambda name, n: self._progress(scan_id, "indexing", detail=name, done=n),
        )
        db.expire_all()  # indexing committed through other sessions
        mark_stale(db, cfg.scan.stale_after_hours, utcnow())
        servers = {s.machine_id: s for s in db.scalars(select(Server)) if s.machine_id != home}
        # Disabled servers keep their old reachability in the DB; reporting them as
        # "disabled" (not live) keeps their copies stale instead of looking lost.
        disabled = {o.id for o in cfg.servers if not o.enabled}
        server_status = {
            sid: "disabled" if sid in disabled else s.reachability for sid, s in servers.items()
        }

        # Instances: read and persist each enabled one.
        enabled = [i for i in cfg.instances if i.enabled]
        instance_ok: dict[str, bool] = {}
        ignore_tag: dict[str, int | None] = {}
        for n, inst in enumerate(enabled):
            self._progress(scan_id, "instances", detail=inst.name, done=n, total=len(enabled))
            clients[inst.id] = client = self.client_factory(inst)
            snap = read_instance(inst, client)
            instance_ok[inst.id] = snap.ok
            if snap.ok:
                persist_snapshot(db, snap, scan_id)
            ignore_tag[inst.id] = next(
                (tid for tid, label in snap.tags.items() if label == cfg.tags.ignore), None
            )

        # Matching against every other enabled server's index.
        self._progress(scan_id, "matching")
        keep, keep_relevant = keep_keys(db)
        excluded = {home} | disabled
        rows = list(db.scalars(select(IndexItem)))
        movie_index = MovieIndex.build(rows, excluded)
        show_index = ShowIndex.build(rows, excluded)
        connections: dict[str, PlexConnection | None] = {}

        def connect(sid: str) -> PlexConnection | None:
            if sid not in connections:
                srv = servers.get(sid)
                live = srv is not None and server_status.get(sid) in _LIVE
                connections[sid] = (
                    connect_server(gateway, srv, cfg.scan.server_timeout_seconds) if live else None
                )
            return connections[sid]

        computed: list[ComputedFinding] = []
        unfetched: set[tuple[str, int, str]] = set()
        ignored_items: set[tuple[str, int]] = set()
        for n, inst in enumerate(enabled):
            if not instance_ok[inst.id]:
                continue
            self._progress(scan_id, "matching", detail=inst.name, done=n, total=len(enabled))
            tag = ignore_tag[inst.id]
            items = list(db.scalars(select(TrackedItem).where(TrackedItem.instance_id == inst.id)))
            ignored = [i for i in items if tag is not None and tag in (i.tag_ids or [])]
            ignored_items |= {(i.instance_id, i.arr_id) for i in ignored}
            if inst.type == "radarr":
                computed += match_movies(items, movie_index, server_status, tag, keep)
                continue
            try:
                computed += match_instance_series(
                    db, clients[inst.id], inst.id, [i for i in items if i not in ignored],
                    show_index, server_status, keep_relevant, started, connect, unfetched,
                )
            except ArrError as e:
                # Missing episode data would look like losses: marking the instance not ok
                # makes reconcile leave its findings untouched. Episodes already persisted
                # stay committed; the rollback only discards this failed step's session state.
                log.warning("instance %s episode read failed: %s", inst.id, e.message)
                instance_ok[inst.id] = False
                db.rollback()

        self._progress(scan_id, "reconcile")
        result = reconcile(
            db, computed, scan_id, server_status, instance_ok,
            {i.id for i in cfg.instances}, ignored_items, now=started, unfetched=unfetched,
        )

        ctx = ScanContext(scan_id, db, cfg, server_status, instance_ok, result, clients,
                          self.store)
        hook_errors = self._run_hooks(ctx)

        active = db.scalar(
            select(func.count()).select_from(Finding).where(Finding.lifecycle == "active")
        )
        return {
            "counts": {
                "new": len(result.new_ids), "lost": len(result.lost_ids),
                "removed": len(result.removed_ids), "resolved": len(result.resolved_ids),
                "updated": len(result.updated_ids), "findings_active": active,
            },
            "servers": server_status,
            "instances": instance_ok,
            "hook_errors": hook_errors,
        }

    def _run_hooks(self, ctx: ScanContext) -> list[dict[str, str]]:
        self._progress(ctx.scan_id, "hooks", total=len(self._hooks))
        errors: list[dict[str, str]] = []
        for n, hook in enumerate(self._hooks):
            name = getattr(hook, "__name__", type(hook).__name__)
            self._progress(ctx.scan_id, "hooks", detail=name, done=n, total=len(self._hooks))
            try:
                hook(ctx)
            except Exception as e:  # noqa: BLE001 - a broken hook must not fail the scan
                ctx.db.rollback()
                msg = redact(str(e)) or type(e).__name__
                log.error("scan %s hook %s failed: %s", ctx.scan_id, name, msg)
                errors.append({"hook": name, "error": msg})
        return errors
