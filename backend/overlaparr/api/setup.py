"""First-run setup and login/logout."""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr import auth
from overlaparr.api.deps import get_db, get_store, require_auth
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import User
from overlaparr.logging_setup import register_secret

setup_router = APIRouter(prefix="/setup", tags=["setup"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_LOGIN = "Invalid username or password"


class Credentials(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class ApiKeyResult(BaseModel):
    api_key: str


def _setup_open(db: Session = Depends(get_db)) -> None:
    auth.require_setup_open(db)


def _log_in(response: Response, store: ConfigStore, user: User) -> dict:
    token = auth.issue_session(store.get_secret("session_secret"), user.username,
                               user.password_hash)
    auth.set_session_cookie(response, token)
    return {"username": user.username}


def _user(db: Session, username: str) -> User | None:
    return db.scalars(select(User).where(User.username == username)).first()


@setup_router.get("/status")
def setup_status(db: Session = Depends(get_db), store: ConfigStore = Depends(get_store)):
    cfg = store.current
    return {
        "needs_user": auth.needs_setup(db),
        "plex_connected": bool(cfg.plex.token),
        "home_server_set": bool(cfg.plex.home_server),
        "instances_count": len(cfg.instances),
    }


@setup_router.post("/user", dependencies=[Depends(_setup_open)])
def setup_user(body: Credentials, response: Response, db: Session = Depends(get_db),
               store: ConfigStore = Depends(get_store)):
    try:
        auth.create_user(db, body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _log_in(response, store, _user(db, body.username.strip()))


@auth_router.post("/login")
def login(body: Credentials, response: Response, db: Session = Depends(get_db),
          store: ConfigStore = Depends(get_store)):
    username = body.username.strip()
    if not auth.verify(db, username, body.password):
        raise HTTPException(status_code=401, detail=INVALID_LOGIN)
    return _log_in(response, store, _user(db, username))


@auth_router.post("/logout", dependencies=[Depends(require_auth)])
def logout(response: Response):
    auth.clear_session_cookie(response)
    return {"ok": True}


WRONG_PASSWORD = "Current password is incorrect"
PASSWORD_ONLY = "Sign in with your password to change it"


@auth_router.post("/password")
def change_password(body: PasswordChange, response: Response,
                    principal: str = Depends(require_auth), db: Session = Depends(get_db),
                    store: ConfigStore = Depends(get_store)) -> dict:
    """Change the user's password; only a signed-in user (not api-key/local) may."""
    user = _user(db, principal)
    if user is None:
        raise HTTPException(status_code=403, detail=PASSWORD_ONLY)
    if not auth.verify(db, user.username, body.current_password):
        raise HTTPException(status_code=403, detail=WRONG_PASSWORD)
    try:
        auth.set_password(db, user, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    # The old cookie is bound to the old hash; re-issue so this caller stays signed in.
    auth.set_session_cookie(
        response, auth.issue_session(store.get_secret("session_secret"), user.username,
                                     user.password_hash))
    return {"ok": True}


@auth_router.post("/api-key/regenerate", dependencies=[Depends(require_auth)])
def regenerate_api_key(store: ConfigStore = Depends(get_store)) -> ApiKeyResult:
    """Issue a new API key, returned once; the old one stops working at once."""
    key = _secrets.token_urlsafe(32)
    store.set_secret("app_api_key", key)
    register_secret(key)
    return ApiKeyResult(api_key=key)


@auth_router.get("/me")
def me(principal: str = Depends(require_auth)):
    return {"principal": principal}
