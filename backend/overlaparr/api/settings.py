"""General settings, raw config.yaml download and cron preview."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from overlaparr.api.deps import config_error, ensure_config_writable, get_store, update_config
from overlaparr.config.schema import AppConfig
from overlaparr.config.store import ConfigStore, mask_secrets
from overlaparr.cron import next_runs

router = APIRouter(prefix="/settings", tags=["settings"])

SECTIONS = ("scan", "tags", "logging", "action_log", "auth")
NOTIFICATION_FIELDS = {"app_url"}
PREVIEW_RUNS = 5


def _settings(store: ConfigStore) -> dict:
    return {**mask_secrets(store.current), "config_error": config_error(store)}


def _check_body(body: dict[str, Any]) -> None:
    for key, value in body.items():
        if key not in SECTIONS and key != "notifications":
            raise HTTPException(status_code=422, detail=f"{key!r} cannot be changed here")
        if not isinstance(value, dict):
            raise HTTPException(status_code=422, detail=f"{key!r} must be an object")
    extra = set(body.get("notifications", {})) - NOTIFICATION_FIELDS
    if extra:
        raise HTTPException(
            status_code=422, detail=f"notifications: only {sorted(NOTIFICATION_FIELDS)} can be changed here"
        )


@router.get("")
def get_settings(store: ConfigStore = Depends(get_store)):
    return _settings(store)


@router.put("")
def put_settings(body: dict[str, Any] = Body(...), store: ConfigStore = Depends(get_store)):
    ensure_config_writable(store)
    _check_body(body)

    def mutate(c: AppConfig) -> None:
        for key in SECTIONS:
            if key in body:
                section = getattr(c, key)
                merged = {**section.model_dump(), **body[key]}
                setattr(c, key, type(section).model_validate(merged))
        if "app_url" in body.get("notifications", {}):
            c.notifications.app_url = body["notifications"]["app_url"]

    update_config(store, mutate)
    return _settings(store)


@router.get("/config.yaml")
def download_config(store: ConfigStore = Depends(get_store)):
    # Secrets live in secrets.yaml, so config.yaml never contains them.
    return Response(
        content=store.config_path.read_bytes(),
        media_type="text/yaml",
        headers={"Content-Disposition": 'attachment; filename="config.yaml"'},
    )


@router.get("/cron-preview")
def cron_preview(cron: str, request: Request):
    try:
        runs = next_runs(cron, PREVIEW_RUNS, request.app.state.scheduler.tz)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"next": [r.isoformat() for r in runs]}
