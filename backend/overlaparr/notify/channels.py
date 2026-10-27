"""Notification delivery: email, Discord, webhook and Apprise."""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import apprise
import httpx
from sqlalchemy.orm import Session

from overlaparr.config.schema import (
    AppriseConn,
    DiscordConn,
    EmailConn,
    NotificationConnection,
    WebhookConn,
)
from overlaparr.db.models import NotificationLog
from overlaparr.logging_setup import redact

HTTP_TIMEOUT = 15
SMTP_TIMEOUT = 20
DISCORD_LIMIT = 2000

log = logging.getLogger(__name__)


class NotifyError(Exception):
    """Delivery failed; the message is already redacted."""


@dataclass
class Message:
    title: str
    text: str
    html: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def _secrets(conn: NotificationConnection) -> list[str]:
    if isinstance(conn, DiscordConn):
        vals = [conn.webhook_url]
    elif isinstance(conn, WebhookConn):
        vals = [conn.url]
    elif isinstance(conn, AppriseConn):
        vals = list(conn.urls)
    else:
        vals = [conn.password or ""]
    return sorted((v for v in vals if v), key=len, reverse=True)


def _clean(conn: NotificationConnection, text: str) -> str:
    for s in _secrets(conn):
        text = text.replace(s, "***")
    return redact(text)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _send_email(conn: EmailConn, msg: Message) -> None:
    m = EmailMessage()
    m["Subject"] = msg.title
    m["From"] = conn.from_addr
    m["To"] = ", ".join(conn.to)
    m.set_content(msg.text)
    if msg.html:
        m.add_alternative(msg.html, subtype="html")
    ctx = ssl.create_default_context()
    if conn.tls == "ssl":
        cm = smtplib.SMTP_SSL(conn.host, conn.port, timeout=SMTP_TIMEOUT, context=ctx)
    else:
        cm = smtplib.SMTP(conn.host, conn.port, timeout=SMTP_TIMEOUT)
    with cm as smtp:
        if conn.tls == "starttls":
            smtp.starttls(context=ctx)
        if conn.username:
            smtp.login(conn.username, conn.password or "")
        smtp.send_message(m)


def _send_discord(conn: DiscordConn, msg: Message) -> None:
    content = _truncate(f"**{msg.title}**\n{msg.text}", DISCORD_LIMIT)
    r = httpx.post(conn.webhook_url, json={"content": content}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()


def _send_webhook(conn: WebhookConn, msg: Message) -> None:
    body = {
        "event": msg.payload.get("event"),
        "title": msg.title,
        "text": msg.text,
        "data": msg.payload,
    }
    r = httpx.request(
        conn.method, conn.url, json=body, headers=conn.headers, timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()


def _send_apprise(conn: AppriseConn, msg: Message) -> None:
    ap = apprise.Apprise()
    for url in conn.urls:
        ap.add(url)
    if not ap.notify(title=msg.title, body=msg.text):
        raise RuntimeError("Apprise failed to deliver the notification")


def send(conn: NotificationConnection, msg: Message) -> None:
    """Deliver ``msg`` via ``conn`` (regardless of ``conn.enabled``)."""
    try:
        if isinstance(conn, EmailConn):
            _send_email(conn, msg)
        elif isinstance(conn, DiscordConn):
            _send_discord(conn, msg)
        elif isinstance(conn, WebhookConn):
            _send_webhook(conn, msg)
        elif isinstance(conn, AppriseConn):
            _send_apprise(conn, msg)
        else:
            raise ValueError(f"unknown connection type: {type(conn).__name__}")
    except Exception as e:
        raise NotifyError(_clean(conn, str(e) or type(e).__name__)) from None


def send_with_retry(
    db: Session,
    conn: NotificationConnection,
    msg: Message,
    event: str,
    attempts: int = 3,
    backoff: tuple[float, ...] = (1, 4),
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Send with retries; log one NotificationLog row. Never raises."""
    attempts = max(1, attempts)
    error: str | None = None
    success = False
    for i in range(attempts):
        try:
            send(conn, msg)
            success, error = True, None
            break
        except NotifyError as e:
            error = str(e)
        except Exception as e:  # defensive: never let a channel bug escape
            error = _clean(conn, str(e) or type(e).__name__)
        if i < attempts - 1 and backoff:
            sleep(backoff[min(i, len(backoff) - 1)])
    try:
        db.add(
            NotificationLog(connection_id=conn.id, event=event, success=success, error=error)
        )
        db.commit()
    except Exception as e:
        log.warning("could not write notification log: %s", _clean(conn, str(e)))
        try:
            db.rollback()
        except Exception:
            pass
    return success
