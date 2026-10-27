"""Scheduled digest: a summary of the library state and recent activity."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.config.schema import NotificationConnection
from overlaparr.db.models import ActionLog, Finding, NotificationLog, ScanRun, Server, utcnow
from overlaparr.notify.channels import Message, send_with_retry
from overlaparr.stats import format_bytes, reclaimable_bytes

log = logging.getLogger(__name__)

DEFAULT_WINDOW = timedelta(days=7)
_LIVE = {"online", "degraded"}


def _link(app_url: str | None, query: str = "") -> str | None:
    if not app_url:
        return None
    return f"{app_url.rstrip('/')}/findings" + (f"?{query}" if query else "")


def build_digest(db: Session, since: datetime, app_url: str | None) -> Message:
    active = list(db.scalars(select(Finding).where(Finding.lifecycle == "active")))
    totals: dict[str, dict[str, int]] = {}
    for f in active:
        by_cov = totals.setdefault(f.type, {})
        by_cov[f.coverage] = by_cov.get(f.coverage, 0) + 1

    since_scan_ids = list(db.scalars(select(ScanRun.id).where(ScanRun.started_at >= since)))
    new = sum(1 for f in active if f.first_seen_scan_id in since_scan_ids)
    lost = db.scalar(select(func.count()).select_from(Finding).where(
        Finding.lifecycle == "lost", Finding.updated_at >= since)) or 0
    auto = db.scalar(select(func.count()).select_from(ActionLog).where(
        ActionLog.actor == "auto", ActionLog.result == "ok", ActionLog.ts >= since)) or 0
    reclaim = reclaimable_bytes(db)
    servers = list(db.scalars(select(Server).where(Server.is_home.is_(False)).order_by(Server.name)))
    healthy = sum(1 for s in servers if s.reachability in _LIVE)

    lines = [f"Since {since:%Y-%m-%d %H:%M} UTC", ""]
    lines.append(f"Active findings: {len(active)}")
    for t in sorted(totals):
        parts = ", ".join(f"{n} {c}" for c, n in sorted(totals[t].items()))
        lines.append(f"  {t}: {sum(totals[t].values())} ({parts})")
    lines += [
        f"New findings: {new}",
        f"Lost findings: {lost}",
        f"Automation actions: {auto}",
        f"Reclaimable space: {format_bytes(reclaim)}",
        f"Servers healthy: {healthy}/{len(servers)}",
    ]
    for s in servers:
        if s.reachability not in _LIVE:
            lines.append(f"  {s.name}: {s.reachability}")
    main_link = _link(app_url)
    if main_link:
        lines += ["", f"Open Overlaparr: {main_link}"]
    text = "\n".join(lines)

    rows = [("Active findings", str(len(active)), _link(app_url)),
            ("New findings", str(new), _link(app_url, "review_state=new")),
            ("Lost findings", str(lost), _link(app_url, "lifecycle=lost")),
            ("Automation actions", str(auto), None),
            ("Reclaimable space", format_bytes(reclaim), _link(app_url, "type=on_disk")),
            ("Servers healthy", f"{healthy}/{len(servers)}", None)]
    body = "".join(
        f"<tr><td>{escape(k)}</td><td>" + (
            f'<a href="{escape(link, quote=True)}">{escape(v)}</a>' if link else escape(v)
        ) + "</td></tr>" for k, v, link in rows)
    html = f"<h2>Overlaparr digest</h2><p>Since {since:%Y-%m-%d %H:%M} UTC</p><table>{body}</table>"
    payload = {
        "event": "digest", "since": since.isoformat(), "active": len(active),
        "totals": totals, "new": new, "lost": lost, "automation_actions": auto,
        "reclaimable_bytes": reclaim, "servers_healthy": healthy, "servers_total": len(servers),
    }
    return Message("Overlaparr digest", text, html, payload)


def last_digest_time(db: Session, connection_id: str) -> datetime | None:
    return db.scalar(select(func.max(NotificationLog.ts)).where(
        NotificationLog.connection_id == connection_id, NotificationLog.event == "digest",
        NotificationLog.success.is_(True)))


def send_digest(db: Session, conn: NotificationConnection, since: datetime | None = None,
                app_url: str | None = None) -> bool:
    """Build and send a digest; ``since`` defaults to the last successful digest, else 7 days."""
    since = since or last_digest_time(db, conn.id) or (utcnow() - DEFAULT_WINDOW)
    return send_with_retry(db, conn, build_digest(db, since, app_url), "digest")
