"""Plex sign-in: PIN flow, pasted-token fallback and sign-out."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from overlaparr.api.deps import get_db, get_store, update_config
from overlaparr.config.store import ConfigStore
from overlaparr.plex import auth as plex_auth

router = APIRouter(prefix="/plex", tags=["plex"])

TOKEN_SECRET = "plex_token"


class TokenIn(BaseModel):
    token: str


def _plex_error(e: plex_auth.PlexAuthError) -> HTTPException:
    if e.status == 404:
        return HTTPException(status_code=404, detail="PIN not found or expired")
    return HTTPException(status_code=502, detail=str(e))


def save_token(store: ConfigStore, token: str) -> None:
    """Save the token; the store writes it to secrets.yaml (as plex_token) and
    references it from config.yaml as ``!secret plex_token``, and only once the
    new config validated, so a failed save leaves no orphaned secret."""
    update_config(store, lambda c: setattr(c.plex, "token", token))


@router.post("/pin")
def create_pin(db: Session = Depends(get_db)):
    try:
        return plex_auth.start_pin(plex_auth.client_identifier(db))
    except plex_auth.PlexAuthError as e:
        raise _plex_error(e) from None


@router.get("/pin/{pin_id}")
def poll_pin(pin_id: int, db: Session = Depends(get_db),
             store: ConfigStore = Depends(get_store)):
    try:
        token = plex_auth.check_pin(pin_id, plex_auth.client_identifier(db))
    except plex_auth.PlexAuthError as e:
        raise _plex_error(e) from None
    if token:
        save_token(store, token)
    return {"connected": bool(token)}


@router.put("/token")
def put_token(body: TokenIn, store: ConfigStore = Depends(get_store)):
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="token must not be empty")
    save_token(store, token)
    return {"connected": True}


@router.delete("/token")
def delete_token(store: ConfigStore = Depends(get_store)):
    update_config(store, lambda c: setattr(c.plex, "token", None))
    store.delete_secret(TOKEN_SECRET)
    return {"connected": False}
