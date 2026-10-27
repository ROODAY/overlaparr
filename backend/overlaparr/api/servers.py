"""Plex servers: discovery, home server choice and per-server overrides."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from overlaparr.api.deps import get_db, get_engine, get_store, update_config
from overlaparr.config.schema import AppConfig, ServerOverride
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import Finding, Server
from overlaparr.plex.gateway import PlexUnreachable
from overlaparr.plex.indexer import refresh_servers
from overlaparr.scan.engine import ScanEngine

router = APIRouter(prefix="/servers", tags=["servers"])


class HomeIn(BaseModel):
    machine_id: str


class ServerPatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None


def _row(s: Server, cfg: AppConfig) -> dict:
    override = next((o for o in cfg.servers if o.id == s.machine_id), None)
    enabled = override.enabled if override else True
    return {
        "machine_id": s.machine_id,
        "name": s.name,
        "label": (override.label if override and override.label else s.name),
        "enabled": enabled,
        "is_home": s.machine_id == cfg.plex.home_server,
        "is_new": s.is_new,
        "status": s.reachability if enabled else "disabled",
        "reachability": s.reachability,
        "last_indexed_at": s.last_indexed_at,
        "last_attempt_at": s.last_attempt_at,
        "movie_count": s.movie_count,
        "show_count": s.show_count,
        "unmatchable_count": s.unmatchable_count,
    }


def _rows(db: Session, cfg: AppConfig) -> list[dict]:
    servers = db.scalars(select(Server)).all()
    rows = [_row(s, cfg) for s in servers]
    return sorted(rows, key=lambda r: (not r["is_home"], r["label"].lower()))


def _known(db: Session, machine_id: str) -> Server:
    srv = db.get(Server, machine_id)
    if srv is None:
        raise HTTPException(status_code=404, detail="Unknown server")
    return srv


@router.get("")
def list_servers(db: Session = Depends(get_db), store: ConfigStore = Depends(get_store)):
    return _rows(db, store.current)


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db),
            store: ConfigStore = Depends(get_store)):
    cfg = store.current
    if not cfg.plex.token:
        raise HTTPException(status_code=409, detail="Plex is not connected")
    gateway = request.app.state.gateway_factory(cfg.plex.token)
    try:
        refresh_servers(db, gateway, cfg.plex.home_server, cfg.servers)
    except PlexUnreachable as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    return _rows(db, cfg)


@router.put("/home")
def set_home(body: HomeIn, db: Session = Depends(get_db),
             store: ConfigStore = Depends(get_store), engine: ScanEngine = Depends(get_engine)):
    _known(db, body.machine_id)
    if store.current.plex.home_server == body.machine_id:
        return {"home_server": body.machine_id, "scan_id": None}
    update_config(store, lambda c: setattr(c.plex, "home_server", body.machine_id))
    for srv in db.scalars(select(Server)):
        srv.is_home = srv.machine_id == body.machine_id
    # Findings were computed against the old home: drop them (copies, ignores and
    # rule states cascade) and rebuild with a fresh scan.
    db.execute(delete(Finding))
    db.commit()
    return {"home_server": body.machine_id, "scan_id": engine.start("manual")}


@router.patch("/{machine_id}")
def patch_server(machine_id: str, body: ServerPatch, db: Session = Depends(get_db),
                 store: ConfigStore = Depends(get_store)):
    srv = _known(db, machine_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("enabled") is None:
        changes.pop("enabled", None)

    def mutate(c: AppConfig) -> None:
        override = next((o for o in c.servers if o.id == machine_id), None)
        if override is None:
            override = ServerOverride(id=machine_id)
            c.servers.append(override)
        for field, value in changes.items():
            setattr(override, field, value)

    cfg = update_config(store, mutate)
    srv.is_new = False
    db.commit()
    return _row(srv, cfg)
