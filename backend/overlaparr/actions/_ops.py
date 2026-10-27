"""Per-action *arr operations for the executor.

Each op reads current state first, records it in ``ctx.prior`` as it goes (so a
failure part-way still logs what was already changed), and performs writes only
when ``ctx.dry_run`` is false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from overlaparr.arr.radarr import RadarrClient
from overlaparr.arr.sonarr import SonarrClient
from overlaparr.config.schema import AppConfig
from overlaparr.db.models import Finding

FILES_GONE = "deleted files cannot be restored"


class ActionError(Exception):
    """An action request that cannot be carried out (logged as an error row)."""


@dataclass
class OpContext:
    cfg: AppConfig
    finding: Finding
    client: RadarrClient | SonarrClient
    scope: str
    episode_ids: list[int]
    delete_mode: str
    add_exclusion: bool
    dry_run: bool
    prior: dict[str, Any] = field(default_factory=dict)

    @property
    def is_movie(self) -> bool:
        return self.finding.kind == "movie"

    def write(self, fn, *args) -> None:
        if not self.dry_run:
            fn(*args)

    def read_item(self) -> dict:
        f = self.finding
        return self.client.get_movie(f.arr_id) if self.is_movie else self.client.get_series(f.arr_id)


@dataclass
class Outcome:
    undoable: bool
    message: str | None = None


def _file_entry(f: dict) -> dict:
    quality = ((f.get("quality") or {}).get("quality") or {}).get("name")
    return {"file_id": f["id"], "path": f.get("path"), "size": f.get("size"), "quality": quality}


def unmonitor(ctx: OpContext) -> Outcome:
    f, c = ctx.finding, ctx.client
    if ctx.scope == "episodes":
        ids = [e["id"] for e in c.get_episodes(ctx.episode_ids) if e.get("monitored")]
        ctx.prior["monitored"] = {str(i): True for i in ids}
        if ids:
            ctx.write(c.set_episodes_monitored, ids, False)
    else:
        changed = bool(ctx.read_item().get("monitored"))
        ctx.prior["monitored"] = {str(f.arr_id): True} if changed else {}
        if changed:
            setter = c.set_movies_monitored if ctx.is_movie else c.set_series_monitored
            ctx.write(setter, [f.arr_id], False)
    if not ctx.prior["monitored"]:
        return Outcome(False, "already unmonitored")
    return Outcome(True)


def delete_files(ctx: OpContext) -> Outcome:
    f, c = ctx.finding, ctx.client
    if ctx.is_movie:
        m = c.get_movie(f.arr_id)
        mf = (m.get("movieFile") or {}) if m.get("hasFile") else {}
        files = [mf] if mf.get("id") else []
        mon_ids = [f.arr_id] if m.get("monitored") else []
        ctx.prior["monitored"] = {str(i): True for i in mon_ids}
        if mon_ids:
            ctx.write(c.set_movies_monitored, mon_ids, False)
        ctx.prior["deleted"] = [_file_entry(x) for x in files]
        for x in files:
            ctx.write(c.delete_movie_file, x["id"])
    else:
        eps = c.episodes(f.arr_id) if ctx.scope == "item" else c.get_episodes(ctx.episode_ids)
        file_ids = sorted({e["episodeFileId"] for e in eps if e.get("episodeFileId")})
        meta = {x["id"]: x for x in c.episode_files(f.arr_id)} if file_ids else {}
        mon_ids = [e["id"] for e in eps if e.get("monitored")]
        ctx.prior["monitored"] = {str(i): True for i in mon_ids}
        if mon_ids:
            ctx.write(c.set_episodes_monitored, mon_ids, False)
        ctx.prior["deleted"] = [_file_entry(meta.get(i) or {"id": i}) for i in file_ids]
        if file_ids:
            ctx.write(c.delete_episode_files, file_ids)
    ctx.prior = {"deleted": ctx.prior["deleted"], "monitored": ctx.prior["monitored"]}
    n = len(ctx.prior["deleted"])
    return Outcome(True, f"{n} file(s) deleted; undo restores monitoring only, {FILES_GONE}")


def remove_item(ctx: OpContext) -> Outcome:
    f, c = ctx.finding, ctx.client
    raw = ctx.read_item()
    if ctx.is_movie:
        ids = {"tmdb": raw.get("tmdbId"), "imdb": raw.get("imdbId")}
        size = raw.get("sizeOnDisk") or (raw.get("movieFile") or {}).get("size")
    else:
        ids = {"tvdb": raw.get("tvdbId"), "tmdb": raw.get("tmdbId"), "imdb": raw.get("imdbId")}
        size = (raw.get("statistics") or {}).get("sizeOnDisk")
    ctx.prior["item"] = {"title": raw.get("title"), "ids": ids, "path": raw.get("path"),
                         "size": size}
    remover = c.delete_movie if ctx.is_movie else c.delete_series
    ctx.write(remover, f.arr_id, ctx.add_exclusion)
    return Outcome(False, "item removed from " + ("Radarr" if ctx.is_movie else "Sonarr"))


def exclude(ctx: OpContext) -> Outcome:
    c = ctx.client
    raw = ctx.read_item()
    ext_id = raw.get("tmdbId") if ctx.is_movie else raw.get("tvdbId")
    if not ext_id:
        raise ActionError("Item has no " + ("TMDB" if ctx.is_movie else "TVDB") + " id")
    if c.find_exclusion(ext_id) is not None:
        return Outcome(False, "already excluded")
    if ctx.dry_run:
        ctx.prior["exclusion_id"] = None
    elif ctx.is_movie:
        ctx.prior["exclusion_id"] = c.add_exclusion(ext_id, raw.get("title", ""), raw.get("year") or 0)
    else:
        ctx.prior["exclusion_id"] = c.add_exclusion(ext_id, raw.get("title", ""))
    return Outcome(True)


def _find_tag(c: RadarrClient | SonarrClient, label: str) -> int | None:
    for tag in c.tags():
        if str(tag.get("label", "")).lower() == label.lower():
            return tag["id"]
    return None


def tag(ctx: OpContext) -> Outcome:
    c, label = ctx.client, ctx.cfg.tags.dupe
    tag_id = _find_tag(c, label) if ctx.dry_run else c.ensure_tag(label)
    if tag_id is not None and tag_id in (ctx.read_item().get("tags") or []):
        return Outcome(False, "already tagged")
    ctx.prior["tags_added"] = [tag_id] if tag_id is not None else []
    if tag_id is None:  # dry run: the tag would be created first
        return Outcome(True, f"tag '{label}' would be created")
    ctx.write(c.edit_tags, [ctx.finding.arr_id], [tag_id], "add")
    return Outcome(True)


ARR_OPS = {"unmonitor": unmonitor, "exclude": exclude, "tag": tag}
