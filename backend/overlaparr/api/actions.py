"""Actions: run one or more action requests from the UI, or preview what they would touch."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.actions.executor import ActionError, ActionRequest, execute, validate_request
from overlaparr.api.activity import log_row
from overlaparr.api.deps import get_db, get_store
from overlaparr.api.findings import file_key
from overlaparr.arr.snapshot import make_client
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import Finding, TrackedEpisode, TrackedItem

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionsBody(BaseModel):
    requests: list[ActionRequest]
    confirm: bool = False

    @model_validator(mode="after")
    def _remove_item_needs_item_scope(self) -> ActionsBody:
        for req in self.requests:
            if req.action == "delete" and req.delete_mode == "remove_item" and req.scope != "item":
                raise ValueError("remove_item is only valid with scope 'item'")
        return self


def _findings(db: Session, body: ActionsBody) -> dict[int, Finding]:
    """The requested findings; 404 before anything runs if any is unknown."""
    ids = {r.finding_id for r in body.requests}
    found = {f.id: f for f in db.scalars(select(Finding).where(Finding.id.in_(ids)))}
    missing = sorted(ids - set(found))
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown finding {missing[0]}")
    return found


@router.post("")
def run_actions(body: ActionsBody, request: Request, db: Session = Depends(get_db),
                store: ConfigStore = Depends(get_store)):
    if not body.confirm and any(r.action == "delete" for r in body.requests):
        raise HTTPException(status_code=400, detail="Delete requires confirmation")
    _findings(db, body)
    client_for = getattr(request.app.state, "client_factory", None) or make_client
    cfg = store.current
    rows = [execute(db, cfg, req, actor="ui", client_for=client_for) for req in body.requests]
    return [log_row(r) for r in rows]


@router.post("/preview")
def preview_actions(body: ActionsBody, db: Session = Depends(get_db),
                    store: ConfigStore = Depends(get_store)):
    """What the requests would touch; requests ``execute`` would reject are left out."""
    cfg = store.current
    findings = _findings(db, body)
    valid = []
    for req in body.requests:
        try:
            validate_request(db, cfg, findings[req.finding_id], req)
        except ActionError:
            continue
        valid.append(req)

    series = {(f.instance_id, f.arr_id) for f in findings.values() if f.kind == "series"}
    by_series: dict[tuple[str, int], dict[int, TrackedEpisode]] = {k: {} for k in series}
    if series:
        for ep in db.scalars(select(TrackedEpisode).where(
            TrackedEpisode.instance_id.in_({k[0] for k in series}),
            TrackedEpisode.series_arr_id.in_({k[1] for k in series}),
        )):
            if (key := (ep.instance_id, ep.series_arr_id)) in by_series:
                by_series[key][ep.episode_arr_id] = ep

    episodes: set[tuple[int, int]] = set()  # (finding id, episode arr id)
    movies: set[int] = set()  # finding ids whose movie file goes
    files: dict[tuple, int] = {}  # file key -> size; each file counted once
    for req in valid:
        f = findings[req.finding_id]
        delete = req.action == "delete"
        if f.kind == "movie":
            if delete:
                movies.add(f.id)
            continue
        eps = by_series[(f.instance_id, f.arr_id)]
        if req.scope == "episodes":
            targets = [eps[e] for e in req.episode_ids if e in eps]
            if delete:
                targets = [ep for ep in targets if ep.has_file]
        elif delete:
            targets = [ep for ep in eps.values() if ep.has_file]
        elif req.action == "unmonitor":
            targets = [eps[e] for e in f.relevant_episode_ids if e in eps]
        else:
            targets = []
        episodes.update((f.id, ep.episode_arr_id) for ep in targets)
        if delete:
            files.update((file_key(ep), ep.file_size or 0) for ep in targets)

    total = sum(files.values())
    for f in (findings[i] for i in movies):
        total += db.scalar(select(TrackedItem.file_size).where(
            TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id)) or 0
    return {"items": len({r.finding_id for r in valid}), "episodes": len(episodes),
            "bytes": total}
