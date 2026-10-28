"""Sonarr/Radarr instances: CRUD (keys masked), connection test, profiles and tags."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from overlaparr.api.deps import get_store, update_config, validation_detail
from overlaparr.arr.client import ArrClient, ArrError
from overlaparr.config.schema import AppConfig, InstanceConfig, LossHandling, new_id
from overlaparr.config.store import MASK, ConfigStore, instance_secret_key, mask_secrets
from overlaparr.logging_setup import redact

router = APIRouter(prefix="/instances", tags=["instances"])


class InstanceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # the UI may send back id etc.

    name: str
    type: Literal["sonarr", "radarr"]
    url: str
    api_key: str
    enabled: bool = True
    loss_handling: LossHandling = LossHandling()


class InstanceTest(BaseModel):
    type: Literal["sonarr", "radarr"]
    url: str
    api_key: str
    id: str | None = None  # with a masked api_key: test using this instance's stored key


def _masked(cfg: AppConfig, instance_id: str) -> dict:
    return next(i for i in mask_secrets(cfg)["instances"] if i["id"] == instance_id)


def _find(cfg: AppConfig, instance_id: str) -> InstanceConfig:
    inst = next((i for i in cfg.instances if i.id == instance_id), None)
    if inst is None:
        raise HTTPException(status_code=404, detail="Unknown instance")
    return inst


def _build(**data: Any) -> InstanceConfig:
    try:
        return InstanceConfig(**data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=validation_detail(e)) from None


NEW_URL_NEEDS_KEY = "Re-enter the API key for the new URL"


def _reuse_key_for(existing: InstanceConfig, inst: InstanceConfig) -> None:
    """A stored key is only reused against the URL it was entered for."""
    if inst.url != existing.url:
        raise HTTPException(status_code=422, detail=NEW_URL_NEEDS_KEY)


def _error(e: ArrError, api_key: str) -> str:
    msg = e.message.replace(api_key, "***") if api_key else e.message
    return redact(msg)


def _call(request: Request, inst: InstanceConfig, fn: Callable[[ArrClient], Any]) -> Any:
    client = request.app.state.client_factory(inst)
    try:
        return fn(client)
    finally:
        client.close()


@router.get("")
def list_instances(store: ConfigStore = Depends(get_store)):
    return mask_secrets(store.current)["instances"]


@router.post("")
def create_instance(body: InstanceIn, store: ConfigStore = Depends(get_store)):
    if body.api_key == MASK:
        raise HTTPException(status_code=422, detail="api_key is required")
    inst = _build(id=new_id(), **body.model_dump())
    cfg = update_config(store, lambda c: c.instances.append(inst))
    return _masked(cfg, inst.id)


@router.post("/test")
def test_instance(body: InstanceTest, request: Request, store: ConfigStore = Depends(get_store)):
    api_key = body.api_key
    existing = None
    if api_key == MASK and body.id:
        existing = _find(store.current, body.id)
        api_key = existing.api_key
    inst = _build(id=body.id or "test", name="test", type=body.type, url=body.url,
                  api_key=api_key)
    if existing is not None:
        _reuse_key_for(existing, inst)
    try:
        status = _call(request, inst, lambda c: c.status())
    except ArrError as e:
        return {"ok": False, "error": _error(e, api_key)}
    return {"ok": True, "version": (status or {}).get("version")}


@router.put("/{instance_id}")
def update_instance(instance_id: str, body: InstanceIn, store: ConfigStore = Depends(get_store)):
    existing = _find(store.current, instance_id)
    data = body.model_dump()
    reused = data["api_key"] == MASK
    if reused:
        data["api_key"] = existing.api_key
    inst = _build(id=instance_id, **data)
    if reused:
        _reuse_key_for(existing, inst)

    def mutate(c: AppConfig) -> None:
        idx = next((n for n, i in enumerate(c.instances) if i.id == instance_id), None)
        if idx is None:
            raise HTTPException(status_code=404, detail="Unknown instance")
        c.instances[idx] = inst

    return _masked(update_config(store, mutate), instance_id)


@router.delete("/{instance_id}")
def delete_instance(instance_id: str, store: ConfigStore = Depends(get_store)):
    _find(store.current, instance_id)
    # Its findings are purged by reconcile on the next scan.
    update_config(store, lambda c: setattr(
        c, "instances", [i for i in c.instances if i.id != instance_id]))
    store.delete_secret(instance_secret_key(instance_id))
    return {"ok": True}


@router.get("/{instance_id}/profiles")
def instance_profiles(instance_id: str, request: Request, store: ConfigStore = Depends(get_store)):
    inst = _find(store.current, instance_id)
    try:
        profiles = _call(request, inst, lambda c: c.quality_profiles())
    except ArrError as e:
        raise HTTPException(status_code=502, detail=_error(e, inst.api_key)) from None
    return [{"id": p["id"], "name": p.get("name")} for p in profiles or []]


@router.get("/{instance_id}/tags")
def instance_tags(instance_id: str, request: Request, store: ConfigStore = Depends(get_store)):
    inst = _find(store.current, instance_id)
    try:
        tags = _call(request, inst, lambda c: c.tags())
    except ArrError as e:
        raise HTTPException(status_code=502, detail=_error(e, inst.api_key)) from None
    return [{"id": t["id"], "label": t.get("label")} for t in tags or []]
