"""Findings: filtered, sorted list; detail with copies or the episode grid; automation hold."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from overlaparr.api.activity import log_row
from overlaparr.api.deps import get_db, get_store
from overlaparr.automation.runner import clear_hold
from overlaparr.config.schema import AppConfig, InstanceConfig
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import (
    ActionLog,
    Finding,
    FindingCopy,
    FindingServerIgnore,
    Server,
    TrackedEpisode,
    TrackedItem,
)
from overlaparr.domain.quality import Res

router = APIRouter(prefix="/findings", tags=["findings"])

_RES_LABELS = {Res.SD: "SD", Res.R480: "480p", Res.R576: "576p", Res.R720: "720p",
               Res.R1080: "1080p", Res.R2160: "2160p"}


def res_label(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return _RES_LABELS[Res(value)]
    except ValueError:
        return None


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _instances(cfg: AppConfig) -> dict[str, InstanceConfig]:
    return {i.id: i for i in cfg.instances}


def _labels(db: Session, cfg: AppConfig) -> dict[str, str]:
    labels = {s.machine_id: s.name for s in db.scalars(select(Server))}
    labels.update({o.id: o.label for o in cfg.servers if o.label})
    return labels


def _label(labels: dict[str, str], server_id: str) -> str:
    return labels.get(server_id, server_id)


def file_key(ep: TrackedEpisode) -> tuple:
    """Identity of an episode's file: several episodes can share one (multi-episode files)."""
    if ep.episode_file_id is not None:
        return (ep.instance_id, "file", ep.episode_file_id)
    return (ep.instance_id, "episode", ep.episode_arr_id)


def _best(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return max(known) if known else None


class _Context:
    """Bulk-loaded rows for a set of findings (avoids per-finding queries)."""

    def __init__(self, db: Session, cfg: AppConfig, findings: list[Finding]) -> None:
        self.instances = _instances(cfg)
        self.labels = _labels(db, cfg)
        ids = [f.id for f in findings]
        self.items: dict[tuple[str, int], TrackedItem] = {}
        self.copies: dict[int, list[FindingCopy]] = defaultdict(list)
        self.ignored: dict[int, set[str]] = defaultdict(set)
        self.episodes: dict[tuple[str, int], TrackedEpisode] = {}
        if not findings:
            return
        keys = {(f.instance_id, f.arr_id) for f in findings}
        for item in db.scalars(select(TrackedItem).where(
            TrackedItem.instance_id.in_({k[0] for k in keys}),
            TrackedItem.arr_id.in_({k[1] for k in keys}),
        )):
            self.items[(item.instance_id, item.arr_id)] = item
        for c in db.scalars(select(FindingCopy).where(FindingCopy.finding_id.in_(ids))
                            .order_by(FindingCopy.id)):
            self.copies[c.finding_id].append(c)
        for ig in db.scalars(select(FindingServerIgnore)
                             .where(FindingServerIgnore.finding_id.in_(ids))):
            self.ignored[ig.finding_id].add(ig.server_id)
        series = [f for f in findings if f.kind == "series"]
        ep_ids = {e for f in series for e in f.relevant_episode_ids}
        if ep_ids:
            for ep in db.scalars(select(TrackedEpisode).where(
                TrackedEpisode.instance_id.in_({f.instance_id for f in series}),
                TrackedEpisode.episode_arr_id.in_(ep_ids),
            )):
                self.episodes[(ep.instance_id, ep.episode_arr_id)] = ep

    def relevant_episodes(self, f: Finding) -> list[TrackedEpisode]:
        eps = [self.episodes.get((f.instance_id, e)) for e in f.relevant_episode_ids]
        return sorted((e for e in eps if e is not None), key=lambda e: (e.season, e.episode))


def _yours(ctx: _Context, f: Finding, item: TrackedItem | None) -> dict:
    if f.kind == "movie":
        return {"resolution": res_label(item.resolution if item else None),
                "size": item.file_size if item else None}
    if f.type != "on_disk":
        return {"resolution": None, "size": None}
    eps = ctx.relevant_episodes(f)
    files = {file_key(e): e.file_size or 0 for e in eps}  # multi-episode files count once
    return {"resolution": res_label(_best([e.resolution for e in eps])),
            "size": sum(files.values())}


def _elsewhere(ctx: _Context, f: Finding) -> list[dict]:
    by_server: dict[str, list[FindingCopy]] = defaultdict(list)
    for c in ctx.copies[f.id]:
        by_server[c.server_id].append(c)
    rows = []
    for server_id, copies in by_server.items():
        best = max(copies, key=lambda c: -1 if c.resolution is None else c.resolution)
        rows.append({
            "server_id": server_id,
            "label": _label(ctx.labels, server_id),
            "resolution": res_label(best.resolution),
            "hdr": best.hdr,
            "stale": all(c.stale for c in copies),
            "ignored": server_id in ctx.ignored[f.id],
        })
    return sorted(rows, key=lambda r: r["label"].lower())


def _arr_url(inst: InstanceConfig, f: Finding, item: TrackedItem | None) -> str | None:
    if item is None or not item.title_slug:
        return None
    kind = "movie" if f.kind == "movie" else "series"
    return f"{inst.url.rstrip('/')}/{kind}/{item.title_slug}"


def finding_row(ctx: _Context, f: Finding) -> dict:
    inst = ctx.instances[f.instance_id]
    item = ctx.items.get((f.instance_id, f.arr_id))
    return {
        "id": f.id,
        "instance": {"id": inst.id, "name": inst.name, "type": inst.type},
        "arr_id": f.arr_id,
        "kind": f.kind,
        "type": f.type,
        "title": item.title if item else f"{f.kind} {f.arr_id}",
        "year": item.year if item else None,
        "poster_url": item.poster_url if item else None,
        "yours": _yours(ctx, f, item),
        "elsewhere": _elsewhere(ctx, f),
        "badges": {
            "coverage": f.coverage,
            "covered_count": f.covered_count,
            "relevant_count": f.relevant_count,
            "best_quality": f.best_quality,
            "edition": f.edition_summary,
            "has_number_matches": f.has_number_matches,
        },
        "review_state": f.review_state,
        "lifecycle": f.lifecycle,
        "automation_hold": f.automation_hold,
        "arr_url": _arr_url(inst, f, item),
    }


def _size_expr():
    """Your on-disk size: movie file, or relevant episode files (each once) for on_disk series."""
    rel = func.json_each(Finding.relevant_episode_ids).table_valued("value")
    file_id = func.coalesce(TrackedEpisode.episode_file_id, -TrackedEpisode.episode_arr_id)
    per_file = (
        select(func.max(TrackedEpisode.file_size).label("size"))
        .select_from(TrackedEpisode)
        .join(rel, TrackedEpisode.episode_arr_id == rel.c.value)
        .where(TrackedEpisode.instance_id == Finding.instance_id)
        .group_by(file_id)
        .correlate(Finding)
        .subquery()
    )
    series_size = select(func.sum(per_file.c.size)).scalar_subquery()
    return case((Finding.kind == "movie", TrackedItem.file_size),
                (Finding.type == "on_disk", series_size), else_=None)


def _servers_expr():
    return (select(func.count(func.distinct(FindingCopy.server_id)))
            .where(FindingCopy.finding_id == Finding.id).scalar_subquery())


@router.get("")
def list_findings(
    type: str | None = None,  # noqa: A002 - public query parameter name
    coverage: str | None = None,
    quality: str | None = None,
    edition: str | None = None,
    match_method: str | None = None,
    instance: str | None = None,
    server: str | None = None,
    review_state: str | None = None,
    lifecycle: str = "active",
    q: str | None = None,
    sort: Literal["title", "year", "size", "updated", "servers"] = "title",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    store: ConfigStore = Depends(get_store),
):
    cfg = store.current
    conds = [Finding.instance_id.in_(list(_instances(cfg)))]
    for column, value in ((Finding.type, type), (Finding.coverage, coverage),
                          (Finding.best_quality, quality), (Finding.edition_summary, edition),
                          (Finding.instance_id, instance),
                          (Finding.review_state, review_state)):
        if values := _csv(value):
            conds.append(column.in_(values))
    if methods := _csv(match_method):
        conds.append(Finding.has_number_matches.in_(
            {m == "number" for m in methods if m in ("number", "id")}))
    if (states := _csv(lifecycle)) and "all" not in states:
        conds.append(Finding.lifecycle.in_(states))
    if servers := _csv(server):
        conds.append(Finding.id.in_(select(FindingCopy.finding_id)
                                    .where(FindingCopy.server_id.in_(servers))))
    if q and q.strip():
        conds.append(func.lower(TrackedItem.title).contains(q.strip().lower(), autoescape=True))

    joined = Finding.__table__.outerjoin(TrackedItem.__table__, and_(
        TrackedItem.instance_id == Finding.instance_id, TrackedItem.arr_id == Finding.arr_id))
    total = db.scalar(select(func.count()).select_from(joined).where(*conds))

    key = {
        "title": func.lower(TrackedItem.title),
        "year": TrackedItem.year,
        "size": _size_expr(),
        "updated": Finding.updated_at,
        "servers": _servers_expr(),
    }[sort]
    key = key.desc() if order == "desc" else key.asc()
    tiebreak = func.lower(TrackedItem.title).asc(), Finding.id.asc()
    findings = list(db.scalars(
        select(Finding).select_from(joined).where(*conds)
        .order_by(key.nulls_last(), *tiebreak)
        .offset((page - 1) * page_size).limit(page_size)))
    ctx = _Context(db, cfg, findings)
    return {"items": [finding_row(ctx, f) for f in findings], "total": total}


def _get_finding(db: Session, cfg: AppConfig, finding_id: int) -> Finding:
    f = db.get(Finding, finding_id)
    if f is None or f.instance_id not in _instances(cfg):
        raise HTTPException(status_code=404, detail="Unknown finding")
    return f


def _copies(ctx: _Context, f: Finding) -> list[dict]:
    ignored = ctx.ignored[f.id]
    rows = [{
        "server_id": c.server_id,
        "label": _label(ctx.labels, c.server_id),
        "resolution": res_label(c.resolution),
        "hdr": c.hdr,
        "codec": c.codec,
        "edition": c.edition,
        "size": c.size,
        "quality": c.quality,
        "edition_cmp": c.edition_cmp,
        "stale": c.stale,
        "ignored": c.server_id in ignored,
    } for c in ctx.copies[f.id]]
    return sorted(rows, key=lambda r: r["label"].lower())


def _episode_grid(ctx: _Context, f: Finding) -> tuple[list[dict], list[dict]]:
    ignored = ctx.ignored[f.id]
    by_episode: dict[int, dict[str, FindingCopy]] = defaultdict(dict)
    for c in ctx.copies[f.id]:
        if c.episode_arr_id is None:
            continue
        held = by_episode[c.episode_arr_id].get(c.server_id)
        if held is None or (c.resolution or -1) > (held.resolution or -1):
            by_episode[c.episode_arr_id][c.server_id] = c

    seasons: dict[int, dict] = {}
    episodes = []
    for ep in ctx.relevant_episodes(f):
        copies = by_episode.get(ep.episode_arr_id, {})
        s = seasons.setdefault(ep.season, {"season": ep.season, "relevant": 0, "covered": 0,
                                           "servers": set()})
        s["relevant"] += 1
        if any(sid not in ignored for sid in copies):
            s["covered"] += 1
        s["servers"].update(copies)
        have = ep.has_file
        episodes.append({
            "episode_arr_id": ep.episode_arr_id,
            "season": ep.season,
            "episode": ep.episode,
            "title": ep.title,
            "yours": {"state": "have" if have else "wanted",
                      "resolution": res_label(ep.resolution) if have else None,
                      "size": ep.file_size if have else None},
            "copies": {sid: {"resolution": res_label(c.resolution), "hdr": c.hdr,
                             "matched_by": c.matched_by, "stale": c.stale,
                             "quality": c.quality}
                       for sid, c in copies.items()},
        })
    season_rows = []
    for _, s in sorted(seasons.items()):
        labels = sorted((_label(ctx.labels, sid) for sid in s["servers"]), key=str.lower)
        season_rows.append({**s, "servers": labels})
    return season_rows, episodes


@router.get("/{finding_id}")
def finding_detail(finding_id: int, db: Session = Depends(get_db),
                   store: ConfigStore = Depends(get_store)):
    cfg = store.current
    f = _get_finding(db, cfg, finding_id)
    ctx = _Context(db, cfg, [f])
    row = finding_row(ctx, f)
    if f.kind == "movie":
        row["copies"] = _copies(ctx, f)
    else:
        row["seasons"], row["episodes"] = _episode_grid(ctx, f)
    row["history"] = [log_row(r) for r in db.scalars(
        select(ActionLog).where(ActionLog.finding_id == f.id)
        .order_by(ActionLog.ts.desc(), ActionLog.id.desc()))]
    return row


class HoldBody(BaseModel):
    hold: bool


@router.post("/{finding_id}/hold")
def set_hold(finding_id: int, body: HoldBody, db: Session = Depends(get_db),
             store: ConfigStore = Depends(get_store)):
    cfg = store.current
    f = _get_finding(db, cfg, finding_id)
    if body.hold:
        f.automation_hold = True
        db.commit()
    else:
        clear_hold(db, cfg, f.id, actor="ui")
    return {"automation_hold": f.automation_hold}
