"""Notification connections: CRUD (secrets masked), test send and delivery history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from overlaparr.api.deps import get_db, get_store, update_config, validation_detail
from overlaparr.config.schema import (
    AppConfig,
    AppriseConn,
    DiscordConn,
    EmailConn,
    NotificationConnection,
    WebhookConn,
    new_id,
)
from overlaparr.config.store import (
    MASK,
    NOTIFY_SECRET_FIELDS,
    ConfigStore,
    mask_secrets,
    notify_header_secret_key,
    notify_secret_key,
)
from overlaparr.db.models import NotificationLog
from overlaparr.logging_setup import redact
from overlaparr.notify.channels import Message, NotifyError, send

router = APIRouter(prefix="/notifications", tags=["notifications"])

READ_ONLY = {"id", "last_sent"}  # server-owned; GET output can be sent back
TEST_TITLE = "Overlaparr test notification"
TEST_TEXT = "If you can see this, notifications are working."
_TYPES = {"email": EmailConn, "discord": DiscordConn, "webhook": WebhookConn,
          "apprise": AppriseConn}
_ADAPTER: TypeAdapter = TypeAdapter(NotificationConnection)


def _mask(value: Any) -> Any:
    return [MASK] * len(value) if isinstance(value, list) else MASK


def _has_mask(value: Any) -> bool:
    return value == MASK or (isinstance(value, list) and MASK in value)


def _masked_copy(existing: dict[str, Any]) -> dict[str, Any]:
    """``existing`` as GET returns it: secrets (and header values) replaced by MASK."""
    out = dict(existing)
    for field in NOTIFY_SECRET_FIELDS:
        if out.get(field) is not None:
            out[field] = _mask(out[field])
    if isinstance(out.get("headers"), dict):
        out["headers"] = {k: MASK for k in out["headers"]}
    return out


def _unmask_headers(data: dict[str, Any], existing: dict[str, Any] | None) -> bool:
    """Swap masked header values for the stored ones (per name); True if any was masked."""
    headers = data.get("headers")
    if not isinstance(headers, dict):
        return False
    kept = (existing or {}).get("headers") or {}
    masked = False
    out = {}
    for name, value in headers.items():
        if value == MASK:
            if name not in kept:
                raise HTTPException(status_code=422,
                                    detail=f"headers.{name} must be entered again")
            value, masked = kept[name], True
        out[name] = value
    data["headers"] = out
    return masked


def _build(conn_id: str, data: dict[str, Any],
           existing: dict[str, Any] | None = None) -> NotificationConnection:
    """Validate a connection; masked secrets keep the stored value from ``existing``.

    A stored secret is only reused while it still goes to the same destination:
    a changed email host or webhook URL needs the password/headers again.
    """
    data = {k: v for k, v in data.items() if k not in READ_ONLY}
    reused: set[str] = set()
    for field in NOTIFY_SECRET_FIELDS:
        value = data.get(field)
        if not _has_mask(value):
            continue
        kept = (existing or {}).get(field)
        if kept is None or value != _mask(kept):
            raise HTTPException(status_code=422, detail=f"{field} must be entered again")
        data[field] = kept
        reused.add(field)
    if _unmask_headers(data, existing):
        reused.add("headers")
    if existing is not None:
        if "password" in reused and data.get("host") != existing.get("host"):
            raise HTTPException(status_code=422, detail="Re-enter the password for the new host")
        if "headers" in reused and data.get("url") != existing.get("url"):
            raise HTTPException(status_code=422, detail="Re-enter the headers for the new URL")
    try:
        return _ADAPTER.validate_python({**data, "id": conn_id})
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_detail(e)) from None


def _index(cfg: AppConfig, conn_id: str) -> int:
    idx = next((n for n, c in enumerate(cfg.notifications.connections) if c.id == conn_id),
               None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Unknown notification connection")
    return idx


def _out(cfg: AppConfig, conn_id: str) -> dict:
    return mask_secrets(cfg)["notifications"]["connections"][_index(cfg, conn_id)]


def _drop_unused_secrets(store: ConfigStore, conn_id: str,
                         conn: NotificationConnection | None = None) -> None:
    """Remove this connection's secrets that ``conn`` (None: deleted) no longer uses."""
    for field in NOTIFY_SECRET_FIELDS:
        key = notify_secret_key(conn_id, field)
        if getattr(conn, field, None) is None and store.get_secret(key) is not None:
            store.delete_secret(key)
    headers = getattr(conn, "headers", None) or {}
    keep = {notify_header_secret_key(conn_id, name) for name in headers}
    prefix = notify_header_secret_key(conn_id, "")
    for key in store.secret_keys():
        if key.startswith(prefix) and key not in keep:
            store.delete_secret(key)


def _replace(store: ConfigStore, conn_id: str,
             make: Callable[[dict[str, Any]], NotificationConnection]) -> dict:
    """Swap connection ``conn_id`` for make(existing stored dict) and save."""
    built: list[NotificationConnection] = []

    def mutate(c: AppConfig) -> None:
        idx = _index(c, conn_id)
        built.append(make(c.notifications.connections[idx].model_dump()))
        c.notifications.connections[idx] = built[0]

    cfg = update_config(store, mutate)
    _drop_unused_secrets(store, conn_id, built[0])
    return _out(cfg, conn_id)


def _scrub(conn: NotificationConnection, text: str) -> str:
    """Hide the connection's own secrets and anything else secret-shaped."""
    values: list[str] = []
    for field in NOTIFY_SECRET_FIELDS:
        value = getattr(conn, field, None)
        values.extend(value if isinstance(value, list) else [value] if value else [])
    values.extend(v for v in (getattr(conn, "headers", None) or {}).values() if v)
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "***")
    return redact(text)


@router.get("")
def list_connections(db: Session = Depends(get_db), store: ConfigStore = Depends(get_store)):
    cfg = store.current
    ids = [c.id for c in cfg.notifications.connections]
    last = dict(db.execute(
        select(NotificationLog.connection_id, func.max(NotificationLog.ts))
        .where(NotificationLog.success.is_(True), NotificationLog.connection_id.in_(ids))
        .group_by(NotificationLog.connection_id)).all()) if ids else {}
    return [{**c, "last_sent": last.get(c["id"])}
            for c in mask_secrets(cfg)["notifications"]["connections"]]


@router.post("", status_code=201)
def create_connection(body: dict[str, Any] = Body(...), store: ConfigStore = Depends(get_store)):
    conn = _build(new_id(), body)
    return _out(update_config(store, lambda c: c.notifications.connections.append(conn)),
                conn.id)


@router.get("/history")
def notification_history(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                         store: ConfigStore = Depends(get_store)):
    names = {c.id: c.name for c in store.current.notifications.connections}
    rows = db.scalars(select(NotificationLog)
                      .order_by(NotificationLog.ts.desc(), NotificationLog.id.desc())
                      .limit(limit))
    return [{"ts": r.ts, "connection_id": r.connection_id,
             "connection_name": names.get(r.connection_id), "event": r.event,
             "success": r.success, "error": r.error} for r in rows]


@router.put("/{conn_id}")
def update_connection(conn_id: str, body: dict[str, Any] = Body(...),
                      store: ConfigStore = Depends(get_store)):
    return _replace(store, conn_id, lambda existing: _build(conn_id, body, existing))


@router.patch("/{conn_id}")
def patch_connection(conn_id: str, body: dict[str, Any] = Body(...),
                     store: ConfigStore = Depends(get_store)):
    def make(existing: dict[str, Any]) -> NotificationConnection:
        model = _TYPES.get(body.get("type", existing["type"]))
        # On a type change keep only the fields the new type has; validation
        # then demands its required ones from the body.
        # Stored secrets enter masked, so _build applies the same reuse rules as PUT.
        base = {k: v for k, v in _masked_copy(existing).items()
                if model is None or k in model.model_fields}
        return _build(conn_id, {**base, **body}, existing)

    return _replace(store, conn_id, make)


@router.delete("/{conn_id}", status_code=204)
def delete_connection(conn_id: str, store: ConfigStore = Depends(get_store)):
    def mutate(c: AppConfig) -> None:
        del c.notifications.connections[_index(c, conn_id)]

    update_config(store, mutate)
    _drop_unused_secrets(store, conn_id)
    return Response(status_code=204)


@router.post("/{conn_id}/test")
def test_connection(conn_id: str, db: Session = Depends(get_db),
                    store: ConfigStore = Depends(get_store)):
    cfg = store.current
    conn = cfg.notifications.connections[_index(cfg, conn_id)]
    error: str | None = None
    try:
        send(conn, Message(title=TEST_TITLE, text=TEST_TEXT))
    except NotifyError as e:
        error = _scrub(conn, str(e))
    db.add(NotificationLog(connection_id=conn.id, event="test", success=error is None,
                           error=error))
    db.commit()
    return {"ok": True} if error is None else {"ok": False, "error": error}
