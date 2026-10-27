"""Actions: run one or more action requests from the UI, or preview what they would touch."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.actions.executor import ActionRequest, execute
from overlaparr.api.activity import log_row
from overlaparr.api.deps import get_db, get_store
from overlaparr.arr.snapshot import make_client
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import Finding, TrackedEpisode, TrackedItem

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionsBody(BaseModel):
    requests: list[ActionRequest]
    confirm: bool = False


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
def preview_actions(body: ActionsBody, db: Session = Depends(get_db)):
    findings = _findings(db, body)
    episodes: set[tuple[int, int]] = set()  # (finding id, episode arr id)
    movie_files: set[int] = set()  # finding ids whose movie file goes
    episode_files: set[tuple[str, int]] = set()  # (instance id, episode arr id)
    series_files: set[tuple[str, int]] = set()  # (instance id, series arr id): every file

    series_ids = {(f.instance_id, f.arr_id) for f in findings.values() if f.kind == "series"}
    by_series: dict[tuple[str, int], list[TrackedEpisode]] = {k: [] for k in series_ids}
    if series_ids:
        for ep in db.scalars(select(TrackedEpisode).where(
            TrackedEpisode.instance_id.in_({k[0] for k in series_ids}),
            TrackedEpisode.series_arr_id.in_({k[1] for k in series_ids}),
        )):
            if (key := (ep.instance_id, ep.series_arr_id)) in by_series:
                by_series[key].append(ep)

    for req in body.requests:
        f = findings[req.finding_id]
        delete = req.action == "delete"
        if f.kind == "movie":
            if delete:
                movie_files.add(f.id)
            continue
        key = (f.instance_id, f.arr_id)
        eps = {e.episode_arr_id: e for e in by_series[key]}
        if req.scope == "episodes":
            episodes.update((f.id, e) for e in req.episode_ids)
            if delete:
                episode_files.update((f.instance_id, e) for e in req.episode_ids)
        elif delete:
            relevant = set(f.relevant_episode_ids)
            episodes.update((f.id, e) for e, ep in eps.items() if e in relevant and ep.has_file)
            series_files.add(key)
        elif req.action == "unmonitor":
            episodes.update((f.id, e) for e in f.relevant_episode_ids)

    total = 0
    for f in (findings[i] for i in movie_files):
        total += db.scalar(select(TrackedItem.file_size).where(
            TrackedItem.instance_id == f.instance_id, TrackedItem.arr_id == f.arr_id)) or 0
    for key in series_files:
        episode_files.update((ep.instance_id, ep.episode_arr_id) for ep in by_series[key])
    sizes = {(ep.instance_id, ep.episode_arr_id): ep.file_size or 0
             for eps in by_series.values() for ep in eps if ep.has_file}
    total += sum(sizes.get(k, 0) for k in episode_files)
    return {"items": len({r.finding_id for r in body.requests}), "episodes": len(episodes),
            "bytes": total}
