import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from overlaparr.config.schema import ServerOverride
from overlaparr.db.models import IndexEpisode, IndexItem, Server
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.domain.quality import Res
from overlaparr.plex.gateway import PlexEpisodeRow, PlexItemRow, PlexUnreachable
from overlaparr.plex.indexer import (
    connect_server,
    ensure_episodes,
    index_servers,
    mark_stale,
    refresh_servers,
)
from tests.fakes import FakePlexGateway


@pytest.fixture
def factory(tmp_path: Path):
    eng = make_engine(tmp_path / "overlaparr.db")
    init_db(eng)
    yield make_sessionmaker(eng)
    eng.dispose()


def movie(rk, tmdb=None, res="1080", size=100, guids=None, **kw):
    g = guids if guids is not None else ([f"tmdb://{tmdb}"] if tmdb else [])
    return PlexItemRow(
        rating_key=str(rk), kind="movie", title=f"M{rk}", year=2020, guids=g,
        resolution=res, hdr=False, codec="h264", edition=None, size=size, **kw,
    )


def show(rk, tvdb):
    return PlexItemRow(
        rating_key=str(rk), kind="show", title=f"S{rk}", year=2020, guids=[f"tvdb://{tvdb}"],
        resolution=None, hdr=False, codec=None, edition=None, size=None,
    )


def ep(season, episode, res="1080", size=10, tvdb=None):
    return PlexEpisodeRow(
        show_rating_key="s1", season=season, episode=episode,
        guids=[f"tvdb://{tvdb}"] if tvdb else [], resolution=res, hdr=False, codec="h264", size=size,
    )


def add_servers(factory, *names, home=None):
    with factory() as db:
        for n in names:
            db.add(Server(machine_id=f"id-{n}", name=n, is_home=(n == home), is_new=False))
        db.commit()
        return list(db.scalars(select(Server).order_by(Server.machine_id)))


def items_of(factory, server_id):
    with factory() as db:
        return list(db.scalars(select(IndexItem).where(IndexItem.server_id == server_id)))


def seed_item(factory, server_id, rk="old", tmdb=999):
    with factory() as db:
        db.add(IndexItem(server_id=server_id, kind="movie", rating_key=rk, title="Old", tmdb=tmdb))
        db.add(IndexEpisode(server_id=server_id, show_rating_key="s9", season=1, episode=1))
        db.commit()


def run(factory, gw, servers, budget=5.0, page_size=2, progress=None, home_id=None):
    return index_servers(
        factory, gw, servers, timeout=1, budget_s=budget, page_size=page_size,
        progress=progress or (lambda label, n: None), home_id=home_id,
    )


def test_index_replaces_items_on_success(factory):
    servers = add_servers(factory, "a")
    seed_item(factory, "id-a")
    gw = FakePlexGateway({"a": {"items": [movie(1, 1), movie(2, 2), movie(3, 3, res="4k"), show(4, 40)]}})
    calls = []
    res = run(factory, gw, servers, progress=lambda label, n: calls.append((label, n)))
    r = res["id-a"]
    assert (r.status, r.items, r.unmatchable, r.error) == ("online", 4, 0, None)
    rows = {i.rating_key: i for i in items_of(factory, "id-a")}
    assert set(rows) == {"1", "2", "3", "4"}
    assert rows["3"].resolution == int(Res.R2160) and rows["3"].tmdb == 3
    assert rows["4"].resolution is None and rows["4"].tvdb == 40 and rows["4"].kind == "show"
    with factory() as db:
        assert db.scalars(select(IndexEpisode)).all() == []
        srv = db.get(Server, "id-a")
        assert srv.reachability == "online"
        assert (srv.movie_count, srv.show_count, srv.unmatchable_count) == (3, 1, 0)
        assert srv.last_indexed_at is not None and srv.last_attempt_at is not None
    assert calls and calls[-1] == ("a", 4)


def test_unreachable_keeps_previous_index(factory):
    servers = add_servers(factory, "a")
    seed_item(factory, "id-a")
    gw = FakePlexGateway({"a": {"unreachable": True, "items": [movie(1, 1)]}})
    r = run(factory, gw, servers)["id-a"]
    assert r.status == "offline" and r.error
    assert [i.rating_key for i in items_of(factory, "id-a")] == ["old"]
    with factory() as db:
        srv = db.get(Server, "id-a")
        assert srv.reachability == "offline" and srv.last_indexed_at is None
        assert len(db.scalars(select(IndexEpisode)).all()) == 1


def test_server_missing_from_discovery_is_offline(factory):
    servers = add_servers(factory, "gone")
    seed_item(factory, "id-gone")
    r = run(factory, FakePlexGateway({}), servers)["id-gone"]
    assert r.status == "offline"
    assert [i.rating_key for i in items_of(factory, "id-gone")] == ["old"]


def test_discovery_failure_marks_all_offline(factory):
    class Down(FakePlexGateway):
        def list_servers(self):
            raise PlexUnreachable("plex.tv down")

    servers = add_servers(factory, "a")
    r = run(factory, Down({"a": {"items": [movie(1, 1)]}}), servers)["id-a"]
    assert r.status == "offline"


def test_budget_exceeded_keeps_previous_index(factory):
    servers = add_servers(factory, "slow", "fast")
    seed_item(factory, "id-slow")
    items = [movie(i, i) for i in range(1, 11)]
    gw = FakePlexGateway({"slow": {"items": items}, "fast": {"items": [movie(1, 1)]}})

    # Per-page delay only for the slow server.
    real_connect = gw.connect

    def connect(info, timeout):
        conn = real_connect(info, timeout)
        if info.name == "slow":
            conn._delay = 0.15
        return conn

    gw.connect = connect
    t0 = time.monotonic()
    res = run(factory, gw, servers, budget=0.4, page_size=1)
    assert time.monotonic() - t0 < 1.0
    assert res["id-slow"].status == "partial"
    assert res["id-fast"].status == "online"
    assert [i.rating_key for i in items_of(factory, "id-fast")] == ["1"]
    time.sleep(0.5)  # let the cancelled worker wind down; nothing may be written
    assert [i.rating_key for i in items_of(factory, "id-slow")] == ["old"]
    with factory() as db:
        assert db.get(Server, "id-slow").reachability == "partial"


def test_failure_mid_scan_is_partial(factory):
    servers = add_servers(factory, "a")
    seed_item(factory, "id-a")
    gw = FakePlexGateway({"a": {"items": [movie(i, i) for i in range(1, 6)], "fail_after_pages": 1}})
    r = run(factory, gw, servers, page_size=2)["id-a"]
    assert r.status == "partial" and r.error
    assert [i.rating_key for i in items_of(factory, "id-a")] == ["old"]


def test_relay_marks_degraded(factory):
    servers = add_servers(factory, "a")
    gw = FakePlexGateway({"a": {"relay": True, "items": [movie(1, 1)]}})
    assert run(factory, gw, servers)["id-a"].status == "degraded"
    assert len(items_of(factory, "id-a")) == 1
    with factory() as db:
        assert db.get(Server, "id-a").reachability == "degraded"


def home_row(factory):
    with factory() as db:
        return db.get(Server, "id-home")


def test_home_server_never_indexed(factory):
    servers = add_servers(factory, "home", "a", home="home")
    gw = FakePlexGateway({"home": {"items": [movie(1, 1)]}, "a": {"items": [movie(2, 2)]}})
    res = run(factory, gw, servers, home_id="id-home")
    assert "id-home" not in res
    assert sorted(gw.connect_calls) == ["a", "home"]
    assert items_of(factory, "id-home") == []
    srv = home_row(factory)
    assert srv.reachability == "online" and srv.last_attempt_at is not None
    assert srv.last_indexed_at is None and srv.movie_count == 0


def test_home_not_contacted_without_home_id(factory):
    servers = add_servers(factory, "home", "a", home="home")
    gw = FakePlexGateway({"home": {}, "a": {"items": [movie(2, 2)]}})
    run(factory, gw, servers)
    assert gw.connect_calls == ["a"]
    assert home_row(factory).last_attempt_at is None


def test_home_check_relay_is_degraded(factory):
    servers = add_servers(factory, "home", "a", home="home")
    gw = FakePlexGateway({"home": {"relay": True}, "a": {}})
    run(factory, gw, servers, home_id="id-home")
    assert home_row(factory).reachability == "degraded"


def _set_home_online(factory):
    with factory() as db:
        db.get(Server, "id-home").reachability = "online"
        db.commit()


def test_home_check_unreachable_is_offline(factory):
    servers = add_servers(factory, "home", "a", home="home")
    _set_home_online(factory)
    gw = FakePlexGateway({"home": {"unreachable": True}, "a": {}})
    run(factory, gw, servers, home_id="id-home")
    srv = home_row(factory)
    assert srv.reachability == "offline" and srv.last_attempt_at is not None


def test_home_missing_from_discovery_is_offline(factory):
    servers = add_servers(factory, "home", "a", home="home")
    _set_home_online(factory)
    run(factory, FakePlexGateway({"a": {}}), servers, home_id="id-home")
    assert home_row(factory).reachability == "offline"


def test_home_check_keeps_index_data(factory):
    servers = add_servers(factory, "home", "a", home="home")
    seed_item(factory, "id-home")
    indexed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    with factory() as db:
        srv = db.get(Server, "id-home")
        srv.movie_count, srv.last_indexed_at = 7, indexed
        db.commit()
    gw = FakePlexGateway({"home": {"items": [movie(1, 1)]}, "a": {}})
    run(factory, gw, servers, home_id="id-home")
    assert [i.rating_key for i in items_of(factory, "id-home")] == ["old"]
    srv = home_row(factory)
    assert srv.movie_count == 7 and srv.last_indexed_at == indexed
    with factory() as db:
        eps = db.scalars(select(IndexEpisode).where(IndexEpisode.server_id == "id-home")).all()
        assert len(eps) == 1


def test_home_pending_at_deadline_is_offline(factory):
    servers = add_servers(factory, "home", home="home")
    _set_home_online(factory)
    gw = FakePlexGateway({"home": {}})
    real_connect = gw.connect

    def connect(info, timeout):
        time.sleep(0.5)
        return real_connect(info, timeout)

    gw.connect = connect
    assert run(factory, gw, servers, budget=0.1, home_id="id-home") == {}
    assert home_row(factory).reachability == "offline"


def test_home_checked_with_no_targets(factory):
    servers = add_servers(factory, "home", home="home")
    gw = FakePlexGateway({"home": {}})
    assert run(factory, gw, servers, home_id="id-home") == {}
    assert gw.connect_calls == ["home"]
    assert home_row(factory).reachability == "online"


def test_duplicate_across_libraries_keeps_best(factory):
    servers = add_servers(factory, "a")
    gw = FakePlexGateway({"a": {"items": [
        movie(1, 7, res="1080", size=900),
        movie(2, guids=["tmdb://7", "imdb://tt7"], res="4k", size=50),
        movie(3, guids=["imdb://tt7"], res="4k", size=60),
    ]}})
    r = run(factory, gw, servers)["id-a"]
    rows = items_of(factory, "id-a")
    assert len(rows) == 1 and r.items == 1
    assert rows[0].resolution == int(Res.R2160) and rows[0].size == 60
    assert rows[0].tmdb == 7 and rows[0].imdb == "tt7"


def test_one_bad_item_never_aborts_a_server_index(factory, monkeypatch):
    from overlaparr.plex import indexer

    servers = add_servers(factory, "a")
    gw = FakePlexGateway({"a": {"items": [movie(1, 1), movie(2, 2), movie(3, 3)]}})
    real = indexer.parse_guids

    def flaky(guids):
        if "tmdb://2" in guids:
            raise ValueError("bad guid")
        return real(guids)

    monkeypatch.setattr(indexer, "parse_guids", flaky)
    r = run(factory, gw, servers)["id-a"]
    assert r.status == "online" and r.items == 2
    assert sorted(i.rating_key for i in items_of(factory, "id-a")) == ["1", "3"]


def test_unmatchable_counted(factory):
    servers = add_servers(factory, "a")
    gw = FakePlexGateway({"a": {"items": [
        movie(1, guids=["com.plexapp.agents.imdb://tt1?lang=en"]),
        movie(2, guids=["local://2"]),
        movie(3, 3),
    ]}})
    r = run(factory, gw, servers)["id-a"]
    assert (r.unmatchable, r.items) == (2, 1)
    assert [i.rating_key for i in items_of(factory, "id-a")] == ["3"]
    with factory() as db:
        assert db.get(Server, "id-a").unmatchable_count == 2


def test_new_server_flagged_and_enabled(factory):
    add_servers(factory, "old")
    gw = FakePlexGateway({"old": {}, "new": {}, "off": {}, "home": {}})
    with factory() as db:
        out = refresh_servers(db, gw, "id-home", [ServerOverride(id="id-off", enabled=False)])
        assert sorted(s.machine_id for s in out) == ["id-new", "id-old"]
        new = db.get(Server, "id-new")
        assert new.is_new is True and new.name == "new"
        assert db.get(Server, "id-old").is_new is False
        assert db.get(Server, "id-off") is not None
        assert [s.machine_id for s in db.scalars(select(Server).where(Server.is_home))] == ["id-home"]


def test_refresh_moves_home_and_keeps_vanished_servers(factory):
    add_servers(factory, "a", "b", home="a")
    with factory() as db:
        db.get(Server, "id-b").reachability = "online"
        db.commit()
    seed_item(factory, "id-b")
    with factory() as db:
        refresh_servers(db, FakePlexGateway({"a": {}}), "id-a", [])
        refresh_servers(db, FakePlexGateway({"a": {}}), None, [])
        b = db.get(Server, "id-b")
        assert b is not None and b.reachability == "offline"
        assert not db.get(Server, "id-a").is_home
    assert len(items_of(factory, "id-b")) == 1


def test_mark_stale_after_48h(factory):
    add_servers(factory, "old", "recent", "up", "never")
    with freeze_time("2026-10-26 12:00:00"):
        now = datetime.now(UTC)
        with factory() as db:
            for mid, reach, age in [
                ("id-old", "offline", 49), ("id-recent", "offline", 47),
                ("id-up", "online", 100), ("id-never", "partial", None),
            ]:
                s = db.get(Server, mid)
                s.reachability = reach
                s.last_indexed_at = None if age is None else now - timedelta(hours=age)
            db.commit()
            mark_stale(db, 48, now)
        with factory() as db:
            got = {s.machine_id: s.reachability for s in db.scalars(select(Server))}
    assert got == {"id-old": "stale", "id-recent": "offline", "id-up": "online", "id-never": "partial"}


def test_mark_stale_skips_home(factory):
    add_servers(factory, "home", "a", home="home")
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    with factory() as db:
        for mid in ("id-home", "id-a"):
            s = db.get(Server, mid)
            s.reachability, s.last_indexed_at = "offline", now - timedelta(hours=100)
        db.commit()
        mark_stale(db, 48, now)
    with factory() as db:
        got = {s.machine_id: s.reachability for s in db.scalars(select(Server))}
    assert got == {"id-home": "offline", "id-a": "stale"}


def _conn(factory, episodes):
    gw = FakePlexGateway({"a": {"items": [show("s1", 1)], "episodes": {"s1": episodes}}})
    add_servers(factory, "a")
    with factory() as db:
        return connect_server(gw, db.get(Server, "id-a"), timeout=1)


def test_ensure_episodes_fetches_dedupes_and_caches(factory):
    conn = _conn(factory, [ep(1, 1, "720", 5, tvdb=11), ep(1, 1, "1080", 1), ep(1, 2, tvdb=12), ep(None, 3)])
    start = datetime.now(UTC) - timedelta(seconds=1)
    with factory() as db:
        db.add(IndexEpisode(server_id="id-a", show_rating_key="s1", season=9, episode=9,
                            fetched_at=start - timedelta(days=1)))
        db.commit()
        rows, fresh = ensure_episodes(db, conn, "id-a", "s1", start)
        assert fresh is True
        assert sorted((r.season, r.episode, r.resolution, r.tvdb_episode_id) for r in rows) == [
            (1, 1, int(Res.R1080), None), (1, 2, int(Res.R1080), 12),
        ]
        stored = db.scalars(select(IndexEpisode).where(IndexEpisode.server_id == "id-a")).all()
        assert len(stored) == 2

        class Boom:
            def episodes(self, key):
                raise AssertionError("should use cache")

        again, fresh2 = ensure_episodes(db, Boom(), "id-a", "s1", start)
        assert fresh2 is True and len(again) == 2


def test_ensure_episodes_failure_returns_cached(factory):
    add_servers(factory, "a")
    start = datetime.now(UTC)
    with factory() as db:
        db.add(IndexEpisode(server_id="id-a", show_rating_key="s1", season=1, episode=1,
                            fetched_at=start - timedelta(days=1)))
        db.commit()

        class Down:
            def episodes(self, key):
                raise PlexUnreachable("x")

        rows, fresh = ensure_episodes(db, Down(), "id-a", "s1", start)
        assert fresh is False and [(r.season, r.episode) for r in rows] == [(1, 1)]
        rows, fresh = ensure_episodes(db, None, "id-a", "s1", start)
        assert fresh is False and len(rows) == 1
        rows, fresh = ensure_episodes(db, None, "id-a", "nope", start)
        assert (rows, fresh) == ([], False)


def test_ensure_episodes_any_fetch_error_is_not_fresh_and_leaves_session_clean(factory):
    add_servers(factory, "a")
    start = datetime.now(UTC)
    with factory() as db:
        class Broken:
            def episodes(self, key):
                raise ValueError("unexpected payload")

        rows, fresh = ensure_episodes(db, Broken(), "id-a", "s1", start)
        assert (rows, fresh) == ([], False)
        assert not db.new and not db.dirty
        db.add(IndexEpisode(server_id="id-a", show_rating_key="s2", season=1, episode=1,
                            fetched_at=start))
        db.commit()  # session still usable


def test_ensure_episodes_write_failure_rolls_back(factory, monkeypatch):
    conn = _conn(factory, [ep(1, 1)])
    start = datetime.now(UTC) - timedelta(seconds=1)
    with factory() as db:
        def fail_commit():
            raise RuntimeError("disk full")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError):
            ensure_episodes(db, conn, "id-a", "s1", start)
        monkeypatch.undo()
        assert not db.new  # rolled back: no half-written episodes pending
        assert db.scalars(select(IndexEpisode)).all() == []


def test_connect_server_none_when_unreachable(factory):
    servers = add_servers(factory, "a", "b")
    gw = FakePlexGateway({"a": {"unreachable": True}})
    assert connect_server(gw, servers[0], timeout=1) is None
    assert connect_server(gw, servers[1], timeout=1) is None  # not discovered
