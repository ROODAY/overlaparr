"""Notification events: queued during a scan, delivered batched afterwards."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from overlaparr.config.schema import AppConfig, NotificationConnection
from overlaparr.db.models import (
    ActionLog,
    Finding,
    PendingEvent,
    ScanRun,
    Server,
    TrackedItem,
)
from overlaparr.logging_setup import redact
from overlaparr.notify.channels import Message, send_with_retry

if TYPE_CHECKING:
    from overlaparr.config.store import ConfigStore
    from overlaparr.scan.engine import ScanContext

log = logging.getLogger(__name__)

_LIVE = {"online", "degraded"}
_PROBLEM = {"offline", "stale", "partial"}


def queue_event(db: Session, kind: str, payload: dict[str, Any]) -> None:
    """Queue an unsent event (committed with the caller's next commit)."""
    db.add(PendingEvent(kind=kind, payload=payload, sent=False))


# ----- queueing ---------------------------------------------------------


def _previous_servers(db: Session, scan_id: int) -> dict[str, str] | None:
    for run in db.scalars(
        select(ScanRun).where(ScanRun.id < scan_id).order_by(ScanRun.id.desc())
    ):
        servers = (run.summary or {}).get("servers")
        if servers is not None:
            return servers
    return None


def _queue_new_findings(ctx: ScanContext) -> None:
    items = []
    for fid in ctx.result.new_ids:
        f = ctx.db.get(Finding, fid)
        if f is None:
            continue
        t = ctx.db.scalar(select(TrackedItem).where(
            TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id))
        items.append({"finding_id": f.id, "title": t.title if t else str(f.arr_id),
                      "type": f.type, "instance_id": f.instance_id, "coverage": f.coverage})
    if items:
        queue_event(ctx.db, "new_findings", {"findings": items})


def _queue_automation(ctx: ScanContext) -> None:
    rows = ctx.db.scalars(select(ActionLog).where(
        ActionLog.actor == "auto", ActionLog.scan_run_id == ctx.scan_id).order_by(ActionLog.id))
    for r in rows:
        queue_event(ctx.db, "automation_action", {
            "action": r.action, "title": r.item_title, "result": r.result,
            "rule_name": r.rule_name, "is_delete": r.action == "delete"})


def _queue_problems(ctx: ScanContext) -> None:
    prev = _previous_servers(ctx.db, ctx.scan_id)
    names = {s.machine_id: s.name for s in ctx.db.scalars(select(Server))}
    for sid, status in sorted(ctx.server_status.items()):
        if status not in _PROBLEM:
            continue
        if prev is not None and prev.get(sid) not in _LIVE:
            continue
        queue_event(ctx.db, "scan_problem", {
            "kind": "server", "id": sid, "name": names.get(sid, sid), "status": status})
    inst_names = {i.id: i.name for i in ctx.config.instances}
    for iid, ok in sorted(ctx.instance_ok.items()):
        if ok is False:
            queue_event(ctx.db, "scan_problem", {
                "kind": "instance", "id": iid, "name": inst_names.get(iid, iid),
                "status": "failed"})


# ----- subscription and formatting -----------------------------------------


def _select_for(conn: NotificationConnection, events: list[PendingEvent]) -> list[PendingEvent]:
    ev = conn.events
    out: list[PendingEvent] = []
    for e in events:
        p = e.payload
        if e.kind == "new_findings":
            if not ev.new_findings:
                continue
            kept = [x for x in p.get("findings", [])
                    if (not ev.new_findings_types or x.get("type") in ev.new_findings_types)
                    and (not ev.new_findings_instances
                         or x.get("instance_id") in ev.new_findings_instances)]
            if kept:
                out.append(PendingEvent(kind=e.kind, payload={"findings": kept}))
        elif e.kind == "automation_action":
            if ev.automation_deletes if p.get("is_delete") else ev.automation_actions:
                out.append(e)
        elif e.kind == "finding_lost" and ev.finding_lost:
            out.append(e)
        elif e.kind == "scan_problem" and ev.scan_problems:
            out.append(e)
        elif e.kind == "app_error" and ev.app_errors:
            out.append(e)
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _lines(kind: str, events: list[PendingEvent]) -> tuple[str, list[str]]:
    """Section heading and its lines for one event kind."""
    if kind == "new_findings":
        items = [x for e in events for x in e.payload["findings"]]
        return (f"New findings ({len(items)})",
                [f"{x['title']} [{x['type']}, {x['coverage']}]" for x in items])
    if kind == "automation_action":
        return (f"Automation actions ({len(events)})", [
            f"{e.payload['action']} {e.payload['title']}: {e.payload['result']}"
            + (f" (rule: {e.payload['rule_name']})" if e.payload.get("rule_name") else "")
            for e in events])
    if kind == "finding_lost":
        out = []
        for e in events:
            p = e.payload
            line = f"{p['title']} no longer on {', '.join(p.get('servers') or ['any server'])}"
            if p.get("actions_taken"):
                line += f" (actions: {', '.join(p['actions_taken'])})"
            out.append(line)
        return f"Lost findings ({len(events)})", out
    if kind == "scan_problem":
        return (f"Scan problems ({len(events)})",
                [f"{e.payload['kind']} {e.payload['name']}: {e.payload['status']}"
                 + (f" ({e.payload['error']})" if e.payload.get("error") else "")
                 for e in events])
    return (f"Errors ({len(events)})",
            [str(e.payload.get("message") or e.payload) for e in events])


_ORDER = ("new_findings", "automation_action", "finding_lost", "scan_problem", "app_error")
_TITLE: dict[str, Callable[[int], str]] = {
    "new_findings": lambda n: _plural(n, "new finding"),
    "automation_action": lambda n: _plural(n, "automation action"),
    "finding_lost": lambda n: f"{n} lost",
    "scan_problem": lambda n: _plural(n, "scan problem"),
    "app_error": lambda n: _plural(n, "error"),
}


def build_message(events: list[PendingEvent], app_url: str | None = None) -> Message:
    by_kind = {k: [e for e in events if e.kind == k] for k in _ORDER}
    counts = {k: (sum(len(e.payload["findings"]) for e in v) if k == "new_findings" else len(v))
              for k, v in by_kind.items() if v}
    title = "Overlaparr: " + ", ".join(_TITLE[k](counts[k]) for k in _ORDER if k in counts)
    text_parts, html_parts = [], []
    for k in _ORDER:
        if not by_kind[k]:
            continue
        heading, lines = _lines(k, by_kind[k])
        text_parts.append(heading + "\n" + "\n".join(f"- {x}" for x in lines))
        html_parts.append(f"<h3>{escape(heading)}</h3><ul>"
                          + "".join(f"<li>{escape(x)}</li>" for x in lines) + "</ul>")
    if app_url:
        link = f"{app_url.rstrip('/')}/findings"
        text_parts.append(f"Open Overlaparr: {link}")
        html_parts.append(f'<p><a href="{escape(link, quote=True)}">Open Overlaparr</a></p>')
    return Message(title, "\n\n".join(text_parts), "".join(html_parts), {
        "event": "batch", "counts": counts,
        "events": [{"kind": e.kind, "payload": e.payload} for e in events]})


# ----- dispatch ---------------------------------------------------------------


def _deliver(db: Session, cfg: AppConfig, events: list[PendingEvent]) -> None:
    """One batched message per enabled, subscribed connection (sending never raises)."""
    notifications = cfg.notifications
    for conn in notifications.connections:
        if not conn.enabled:
            continue
        chosen = _select_for(conn, events)
        if chosen:
            send_with_retry(db, conn, build_message(chosen, notifications.app_url), "batch")


def send_pending(db: Session, cfg: AppConfig) -> None:
    """Deliver every unsent queued event, batched, and mark them sent."""
    pending = list(db.scalars(
        select(PendingEvent).where(PendingEvent.sent.is_(False)).order_by(PendingEvent.id)))
    _deliver(db, cfg, pending)
    for e in pending:
        e.sent = True
    db.commit()


def _send_all(ctx: ScanContext) -> None:
    send_pending(ctx.db, ctx.config)


def dispatch_after_scan(ctx: ScanContext) -> None:
    """Post-scan hook: queue events and send one batched message per connection."""
    if ctx.dry_run:
        return
    try:
        _queue_new_findings(ctx)
        _queue_automation(ctx)
        _queue_problems(ctx)
        ctx.db.commit()
        _send_all(ctx)
    except Exception as e:  # noqa: BLE001 - notifications must never fail a scan
        log.error("notification dispatch failed: %s", redact(str(e)) or type(e).__name__)
        try:
            ctx.db.rollback()
        except Exception:  # noqa: BLE001
            pass


def notify_scan_failed(db: Session, cfg: AppConfig, scan_id: int, error: str) -> None:
    """Scan failure hook: queue a scan problem (error already redacted) and send.

    Also delivers anything the failed scan had queued. Never raises.
    """
    try:
        queue_event(db, "scan_problem", {"kind": "scan", "id": scan_id, "name": f"#{scan_id}",
                                         "status": "failed", "error": redact(error)})
        db.commit()
        send_pending(db, cfg)
    except Exception as e:  # noqa: BLE001 - notifications must never affect a scan
        log.error("scan failure notification failed: %s", redact(str(e)) or type(e).__name__)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


class AppErrorNotifier:
    """Sends an ``app_error`` (e.g. invalid config.yaml) once per distinct message.

    Registered with ``ConfigStore.subscribe_errors``; uses the running (last good)
    config's connections. Delivery runs in a daemon thread by default so a slow
    channel never delays a reload or startup. Never raises.
    """

    def __init__(self, store: ConfigStore, db_factory: sessionmaker[Session],
                 background: bool = True) -> None:
        self._store = store
        self._db_factory = db_factory
        self._background = background
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def __call__(self, message: str) -> None:
        with self._lock:
            if message in self._seen:
                return
            self._seen.add(message)
        if self._background:
            threading.Thread(target=self._send, args=(message,), name="notify-app-error",
                             daemon=True).start()
        else:
            self._send(message)

    def _send(self, message: str) -> None:
        try:
            with self._db_factory() as db:
                event = PendingEvent(kind="app_error", payload={"message": redact(message)},
                                     sent=True)
                db.add(event)
                db.commit()
                _deliver(db, self._store.current, [event])
        except Exception as e:  # noqa: BLE001 - must never affect a reload
            log.error("application error notification failed: %s",
                      redact(str(e)) or type(e).__name__)
