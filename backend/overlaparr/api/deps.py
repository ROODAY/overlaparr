"""Shared FastAPI dependencies and helpers for the API routers."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from overlaparr.config.schema import AppConfig
from overlaparr.config.store import ConfigInvalidOnDisk, ConfigStore
from overlaparr.logging_setup import redact
from overlaparr.scan.engine import ScanEngine


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.db_factory()
    try:
        yield db
    finally:
        db.close()


def get_store(request: Request) -> ConfigStore:
    return request.app.state.store


def get_engine(request: Request) -> ScanEngine:
    return request.app.state.scan_engine


def require_auth(request: Request) -> str:
    """The authenticated principal: a username, "api-key" or "local"."""
    return request.app.state.require_auth(request)


def validation_detail(e: ValidationError) -> list[dict]:
    """Pydantic errors without the submitted input (it may contain secrets)."""
    return [
        {"loc": [str(p) for p in err["loc"]], "msg": redact(err["msg"])}
        for err in e.errors(include_url=False, include_input=False, include_context=False)
    ]


def ensure_config_writable(store: ConfigStore) -> None:
    if store.error:
        raise HTTPException(status_code=409, detail=store.error)


def update_config(store: ConfigStore, mutator: Callable[[AppConfig], None]) -> AppConfig:
    """store.update with API errors: 409 while the file on disk is invalid, 422 on bad input."""
    ensure_config_writable(store)
    try:
        return store.update(mutator)
    except ConfigInvalidOnDisk as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_detail(e)) from None
