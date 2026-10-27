import json
import queue
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from overlaparr.arr.snapshot import make_client
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import Finding, FindingCopy, IndexItem, ScanRun, TrackedEpisode
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.plex.gateway import PlexEpisodeRow, PlexItemRow
from overlaparr.scan.engine import ScanAlreadyRunning, ScanContext, ScanEngine
from overlaparr.scan.progress import PHASES, ProgressBus
from tests.fakes import FakePlexGateway

PAST = (datetime.now(UTC) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
PROFILES = [
    {
        "id": 1, "name": "HD", "cutoff": 3,
        "items": [{"quality": {"id": 3, "name": "WEBDL-1080p", "resolution": 1080},
                   "items": [], "allowed": True}],
    }
]
RADARR = {"id": "r1", "name": "Radarr", "type": "radarr", "url": "http://radarr",
          "api_key": "radarrkey123"}
SONARR = {"id": "s1", "name": "Sonarr", "type": "sonarr", "url": "http://sonarr",
          "api_key": "sonarrkey123"}


def movie_row(rk, tmdb, res="2160"):
    return PlexItemRow(rating_key=str(rk), kind="movie", title=f"M{rk}", year=2020,
                       guids=[f"tmdb://{tmdb}"], resolution=res, hdr=False, codec="hevc",
                       edition=None, size=100)


def show_row(rk, tvdb):
    return PlexItemRow(rating_key=str(rk), kind="show", title=f"S{rk}", year=2020,
                       guids=[f"tvdb://{tvdb}"], resolution=None, hdr=False, codec=None,
                       edition=None, size=None)


def plex_ep(show_rk, season, episode, tvdb):
    return PlexEpisodeRow(show_rating_key=str(show_rk), season=season, episode=episode,
                          guids=[f"tvdb://{tvdb}"], resolution="1080", hdr=False,
                          codec="h264", size=10)


def radarr_movie(arr_id=1, tmdb=603, tags=()):
    return {"id": arr_id, "title": "The Matrix", "year": 1999, "tmdbId": tmdb,
            "monitored": True, "hasFile": False, "qualityProfileId": 1, "tags": list(tags)}


def mock_radarr(movies, tags=()):
    respx.get("http://radarr/api/v3/movie").respond(json=movies)
    respx.get("http://radarr/api/v3/qualityprofile").respond(json=PROFILES)
    respx.get("http://radarr/api/v3/tag").respond(json=list(tags))


def mock_sonarr(series, episodes):
    """``episodes``: series id -> list of Sonarr episode dicts (or an HTTP status to fail)."""
    respx.get("http://sonarr/api/v3/series").respond(json=series)
    respx.get("http://sonarr/api/v3/qualityprofile").respond(json=PROFILES)
    respx.get("http://sonarr/api/v3/tag").respond(json=[])
    for sid, eps in episodes.items():
        route = respx.get("http://sonarr/api/v3/episode", params={"seriesId": sid})
        if isinstance(eps, int):
            route.respond(status_code=eps)
        else:
            route.respond(json=eps)
        respx.get("http://sonarr/api/v3/episodefile", params={"seriesId": sid}).respond(json=[])


def sonarr_series(arr_id, tvdb):
    return {"id": arr_id, "title": f"Show {arr_id}", "year": 2020, "tvdbId": tvdb,
            "monitored": True, "qualityProfileId": 1, "tags": [],
            "statistics": {"episodeFileCount": 0, "sizeOnDisk": 0}}


def sonarr_ep(ep_id, series_id, tvdb):
    return {"id": ep_id, "seriesId": series_id, "seasonNumber": 1, "episodeNumber": ep_id,
            "title": f"E{ep_id}", "tvdbId": tvdb, "monitored": True, "hasFile": False,
            "airDateUtc": PAST}


class Env:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.eng = make_engine(tmp_path / "o.db")
        init_db(self.eng)
        self.factory = make_sessionmaker(self.eng)
        self.gateway = FakePlexGateway({"home": {}, "other": {"items": [movie_row(10, 603)]}})
        self.gateway_tokens: list[str] = []
        self.bus = ProgressBus()
        self.client_factory = make_client

    def configure(self, **cfg):
        data = {"plex": {"token": "plextoken123", "home_server": "id-home"},
                "instances": [RADARR]}
        data.update(cfg)
        (self.tmp_path / "config.yaml").write_text(json.dumps(data), encoding="utf-8")
        self.store = ConfigStore(self.tmp_path / "config.yaml", self.tmp_path / "secrets.yaml")
        self.store.load()

    def engine(self):
        def gateway_factory(token):
            self.gateway_tokens.append(token)
            return self.gateway

        return ScanEngine(self.store, self.factory, gateway_factory,
                          lambda inst: self.client_factory(inst), self.bus)

    def run(self, engine=None):
        scan_id = (engine or self.engine()).run_sync("manual")
        with self.factory() as db:
            return db.get(ScanRun, scan_id)

    def findings(self):
        with self.factory() as db:
            return list(db.scalars(select(Finding).order_by(Finding.id)))


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.configure()
    yield e
    e.eng.dispose()


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def wait_done(q, timeout=10):
    while True:
        event = q.get(timeout=timeout)
        if event["phase"] == "done":
            return event


@respx.mock
def test_end_to_end_movie_scan(env):
    mock_radarr([radarr_movie()])
    run = env.run()
    assert run.status == "ok" and run.error is None and run.finished_at is not None
    assert run.trigger == "manual"
    [f] = env.findings()
    assert (f.instance_id, f.arr_id, f.type, f.kind) == ("r1", 1, "wanted_elsewhere", "movie")
    assert f.best_quality == "better" and f.lifecycle == "active"
    assert run.summary["counts"] == {"new": 1, "lost": 0, "removed": 0, "resolved": 0,
                                     "updated": 0, "findings_active": 1}
    assert run.summary["servers"] == {"id-other": "online"}
    assert run.summary["instances"] == {"r1": True}
    assert run.summary["hook_errors"] == []
    assert env.gateway_tokens == ["plextoken123"]  # one gateway per scan


@respx.mock
def test_home_and_disabled_servers_are_not_matched(env):
    env.gateway.servers = {
        "home": {"items": [movie_row(1, 603)]},
        "off": {"items": [movie_row(2, 603)]},
    }
    env.configure(servers=[{"id": "id-off", "enabled": False}])
    mock_radarr([radarr_movie()])
    run = env.run()
    assert run.status == "ok"
    assert env.findings() == []
    with env.factory() as db:  # disabled server is never indexed
        assert db.scalars(select(IndexItem)).all() == []


@respx.mock
def test_ignore_tagged_movie_is_skipped_and_marked_ignored(env):
    mock_radarr([radarr_movie()])
    env.run()
    respx.reset()
    mock_radarr([radarr_movie(tags=[7])], tags=[{"id": 7, "label": "dupe-ignore"}])
    run = env.run()
    assert run.status == "ok"
    [f] = env.findings()
    assert f.review_state == "ignored" and f.lifecycle == "active"


@respx.mock
def test_tv_scan_fetches_episodes_with_one_connection_per_server(env):
    env.gateway.servers["other"] = {
        "items": [show_row(20, 100), show_row(21, 200)],
        "episodes": {"20": [plex_ep(20, 1, 1, 1001)], "21": [plex_ep(21, 1, 1, 2001)]},
    }
    env.configure(instances=[SONARR])
    mock_sonarr([sonarr_series(5, 100), sonarr_series(6, 200), sonarr_series(7, 999)],
                {5: [sonarr_ep(1, 5, 1001)], 6: [sonarr_ep(2, 6, 2001)]})
    run = env.run()
    assert run.status == "ok", run.error
    found = env.findings()
    assert [(f.arr_id, f.type, f.coverage) for f in found] == [
        (5, "wanted_elsewhere", "full"), (6, "wanted_elsewhere", "full")]
    # one connect while indexing + one cached connect for episode fetches
    assert env.gateway.connect_calls.count("other") == 2
    with env.factory() as db:
        assert {e.series_arr_id for e in db.scalars(select(TrackedEpisode))} == {5, 6}


@respx.mock
def test_episode_fetch_failure_marks_instance_not_ok(env):
    env.gateway.servers["other"] = {
        "items": [show_row(20, 100)],
        "episodes": {"20": [plex_ep(20, 1, 1, 1001)]},
    }
    env.configure(instances=[SONARR])
    mock_sonarr([sonarr_series(5, 100)], {5: [sonarr_ep(1, 5, 1001)]})
    env.run()
    [before] = env.findings()
    respx.reset()
    mock_sonarr([sonarr_series(5, 100)], {5: 500})
    run = env.run()
    assert run.status == "ok"
    assert run.summary["instances"] == {"s1": False}
    [after] = env.findings()
    assert after.lifecycle == "active" and after.last_seen_scan_id == before.last_seen_scan_id


def test_single_scan_at_a_time(env):
    entered, release = threading.Event(), threading.Event()
    env.configure(instances=[])
    engine = env.engine()

    def blocking_hook(ctx):
        entered.set()
        release.wait(10)

    engine.register_post_hook(blocking_hook)
    q = env.bus.subscribe()
    first = engine.start("schedule")
    assert isinstance(first, int)
    assert entered.wait(10)
    assert engine.start("manual") is None
    with pytest.raises(ScanAlreadyRunning):
        engine.run_sync("manual")
    release.set()
    done = wait_done(q)
    assert done["scan_id"] == first and done["status"] == "ok"
    with env.factory() as db:
        assert [r.trigger for r in db.scalars(select(ScanRun))] == ["schedule"]
    assert isinstance(engine.start("manual"), int)  # lock released afterwards
    wait_done(q)


@respx.mock
def test_failure_message_redacted(env):
    def exploding(inst):
        raise RuntimeError("GET http://radarr/api/v3/movie?apikey=SECRET123 failed")

    env.client_factory = exploding
    q = env.bus.subscribe()
    engine = env.engine()
    run = env.run(engine)
    assert run.status == "failed" and run.finished_at is not None
    assert "SECRET123" not in run.error and "apikey" in run.error
    done = drain(q)[-1]
    assert done["phase"] == "done" and done["running"] is False
    assert "SECRET123" not in done["error"]
    env.client_factory = make_client
    mock_radarr([radarr_movie()])
    assert env.run(engine).status == "ok"  # lock was released


@respx.mock
def test_progress_events_emitted(env):
    mock_radarr([radarr_movie()])
    q = env.bus.subscribe()
    env.run()
    events = drain(q)
    phases = list(dict.fromkeys(e["phase"] for e in events))
    assert phases == list(PHASES)
    assert all(e["running"] for e in events[:-1])
    assert events[-1]["running"] is False and events[-1]["status"] == "ok"
    assert env.bus.snapshot()["phase"] == "done"
    env.bus.unsubscribe(q)
    env.run()
    assert drain(q) == []


@respx.mock
def test_hook_failure_does_not_fail_scan(env):
    mock_radarr([radarr_movie()])
    engine = env.engine()
    calls = []

    def bad(ctx):
        calls.append("bad")
        raise ValueError("hook broke token=SECRET123")

    def good(ctx: ScanContext):
        calls.append("good")
        assert ctx.result.new_ids and ctx.instance_ok == {"r1": True}
        assert ctx.server_status == {"id-other": "online"}
        assert set(ctx.clients) == {"r1"} and ctx.store is env.store
        assert ctx.config.instances[0].id == "r1" and ctx.dry_run is False

    engine.register_post_hook(bad)
    engine.register_post_hook(good)
    run = env.run(engine)
    assert run.status == "ok"
    assert calls == ["bad", "good"]
    [err] = run.summary["hook_errors"]
    assert err["hook"] == "bad" and "SECRET123" not in err["error"]


def test_not_configured_fails_cleanly(env):
    env.configure(plex={"token": None, "home_server": None})
    q = env.bus.subscribe()
    run = env.run()
    assert run.status == "failed" and run.error == "Plex not configured"
    assert env.gateway_tokens == []
    events = drain(q)
    assert events[-1]["phase"] == "done" and events[-1]["error"] == "Plex not configured"


def test_plex_tv_unreachable_fails_scan(env):
    class Down(FakePlexGateway):
        def list_servers(self):
            raise httpx.ConnectError("plex.tv down X-Plex-Token=SECRET123")

    env.gateway = Down()
    run = env.run()
    assert run.status == "failed" and "SECRET123" not in run.error


@respx.mock
def test_disabling_a_server_keeps_its_findings_active(env):
    mock_radarr([radarr_movie()])
    env.run()
    [before] = env.findings()
    env.configure(servers=[{"id": "id-other", "enabled": False}])
    run = env.run()
    assert run.status == "ok"
    assert run.summary["servers"] == {"id-other": "disabled"}
    assert run.summary["counts"]["lost"] == 0
    [after] = env.findings()
    assert after.id == before.id and after.lifecycle == "active"
    with env.factory() as db:
        copies = list(db.scalars(select(FindingCopy).where(FindingCopy.finding_id == after.id)))
    assert copies and all(c.stale for c in copies)
