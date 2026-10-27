"""plex.tv PIN sign-in (the popup flow used by Overseerr/Tautulli)."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from overlaparr.db.models import AppState

PINS_URL = "https://plex.tv/api/v2/pins"
AUTH_APP_URL = "https://app.plex.tv/auth"
PRODUCT = "Overlaparr"
CLIENT_ID_KEY = "plex_client_identifier"
TIMEOUT = 15


class PlexAuthError(Exception):
    """plex.tv could not be reached or answered with an error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def client_identifier(db: Session) -> str:
    """This install's stable X-Plex-Client-Identifier (created on first use)."""
    row = db.get(AppState, CLIENT_ID_KEY)
    if row is None:
        row = AppState(key=CLIENT_ID_KEY, value=str(uuid.uuid4()))
        db.add(row)
        db.commit()
    return row.value


def _headers(client_id: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Client-Identifier": client_id,
    }


def _request(method: str, url: str, client_id: str, **kw: Any) -> dict:
    try:
        resp = httpx.request(method, url, headers=_headers(client_id), timeout=TIMEOUT, **kw)
    except httpx.HTTPError as e:
        raise PlexAuthError(f"cannot reach plex.tv: {type(e).__name__}") from None
    if resp.status_code >= 400:
        raise PlexAuthError(f"plex.tv returned HTTP {resp.status_code}", resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise PlexAuthError("plex.tv returned invalid JSON") from None


def auth_url(client_id: str, code: str) -> str:
    query = urlencode(
        {"clientID": client_id, "code": code, "context[device][product]": PRODUCT}
    )
    return f"{AUTH_APP_URL}#?{query}"


def start_pin(client_id: str) -> dict[str, Any]:
    data = _request("POST", PINS_URL, client_id, params={"strong": "true"})
    return {"id": data["id"], "code": data["code"], "auth_url": auth_url(client_id, data["code"])}


def check_pin(pin_id: int, client_id: str) -> str | None:
    """The account token once the user has approved the PIN, else None."""
    data = _request("GET", f"{PINS_URL}/{pin_id}", client_id)
    return data.get("authToken") or None
