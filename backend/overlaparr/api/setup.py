"""First-run setup and login/logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr import auth
from overlaparr.api.deps import get_db, get_store, require_auth
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import User

setup_router = APIRouter(prefix="/setup", tags=["setup"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_LOGIN = "Invalid username or password"


class Credentials(BaseModel):
    username: str
    password: str


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
    if not auth.verify(db, body.username, body.password):
        raise HTTPException(status_code=401, detail=INVALID_LOGIN)
    return _log_in(response, store, _user(db, body.username))


@auth_router.post("/logout", dependencies=[Depends(require_auth)])
def logout(response: Response):
    auth.clear_session_cookie(response)
    return {"ok": True}


@auth_router.get("/me")
def me(principal: str = Depends(require_auth)):
    return {"principal": principal}
