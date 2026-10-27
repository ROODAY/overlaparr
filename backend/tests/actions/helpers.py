"""Shared setup for action executor/undo tests."""

from __future__ import annotations

import json

import respx

from overlaparr.config.schema import AppConfig
from overlaparr.db.models import Finding, TrackedEpisode, TrackedItem

R = "http://radarr"
S = "http://sonarr"
RADARR = {"id": "r1", "name": "My Radarr", "type": "radarr", "url": R, "api_key": "radarrkey123"}
SONARR = {"id": "s1", "name": "My Sonarr", "type": "sonarr", "url": S, "api_key": "sonarrkey123"}
QUALITY = {"quality": {"id": 3, "name": "WEBDL-1080p", "resolution": 1080}}


def make_cfg(*instances) -> AppConfig:
    return AppConfig(instances=list(instances or (RADARR, SONARR)))


def add_finding(db, instance="r1", arr_id=1, kind="movie", title="The Matrix", **kw) -> Finding:
    db.add(TrackedItem(instance_id=instance, arr_id=arr_id, kind=kind, title=title,
                       seen_scan_id=1, tmdb=603, tvdb=81189 if kind == "series" else None))
    f = Finding(instance_id=instance, arr_id=arr_id, kind=kind, type="on_disk",
                coverage="full", best_quality="2160p", edition_summary="same",
                first_seen_scan_id=1, last_seen_scan_id=1, **kw)
    db.add(f)
    db.commit()
    return f


def add_episodes(db, series_id=5, instance="s1", eps=((501, True, 9001), (502, True, None),
                                                      (503, False, 9003))):
    for ep_id, monitored, file_id in eps:
        db.add(TrackedEpisode(instance_id=instance, series_arr_id=series_id, episode_arr_id=ep_id,
                              season=1, episode=ep_id - 500, title=f"E{ep_id}",
                              monitored=monitored, has_file=file_id is not None,
                              episode_file_id=file_id))
    db.commit()


def movie(arr_id=1, monitored=True, has_file=True, tags=()):
    m = {"id": arr_id, "title": "The Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093",
         "monitored": monitored, "hasFile": has_file, "tags": list(tags),
         "path": "/movies/The Matrix (1999)", "sizeOnDisk": 5000}
    if has_file:
        m["movieFile"] = {"id": 77, "movieId": arr_id, "size": 5000,
                          "path": "/movies/The Matrix (1999)/matrix.mkv", "quality": QUALITY}
    return m


def episode(ep_id, monitored, file_id=None, series_id=5):
    return {"id": ep_id, "seriesId": series_id, "seasonNumber": 1, "episodeNumber": ep_id - 500,
            "monitored": monitored, "hasFile": bool(file_id), "episodeFileId": file_id or 0}


def episode_file(file_id, size=1000, series_id=5):
    return {"id": file_id, "seriesId": series_id, "size": size,
            "path": f"/tv/Show/S01/{file_id}.mkv", "quality": QUALITY}


def body(route):
    return json.loads(route.calls.last.request.content)


def non_get_calls():
    return [c for c in respx.calls if c.request.method != "GET"]
