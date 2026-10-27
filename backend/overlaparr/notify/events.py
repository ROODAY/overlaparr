"""Notification event queue: events are stored and delivered later."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from overlaparr.db.models import PendingEvent


def queue_event(db: Session, kind: str, payload: dict[str, Any]) -> None:
    """Queue an unsent event (committed with the caller's next commit)."""
    db.add(PendingEvent(kind=kind, payload=payload, sent=False))
