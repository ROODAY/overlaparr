import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from overlaparr.arr.snapshot import (
    InstanceSnapshot,
    TrackedEpisodeData,
    TrackedItemData,
    make_client,
    persist_episodes,
    persist_snapshot,
    read_instance,
    read_series_episodes,
)
from overlaparr.config.schema import InstanceConfig
from overlaparr.db.models import TrackedEpisode, TrackedItem
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.domain.quality import Res

FIX = Path(__file__).parent.parent / "fixtures"
KEY = "supersecretapikey123"


def load(rel: str):
    return json.loads((FIX / rel).read_text(encoding="utf-8"))


RADARR = InstanceConfig(id="r1", name="R", type="radarr", url="http://radarr", api_key=KEY)
SONARR = InstanceConfig(id="s1", name="S", type="sonarr", url="http://sonarr", api_key=KEY)


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


def mock_radarr():
    respx.get("http://radarr/api/v3/movie").respond(json=load("radarr/movie.json"))
    respx.get("http://radarr/api/v3/qualityprofile").respond(json=load("radarr/qualityprofile.json"))
    respx.get("http://radarr/api/v3/tag").respond(json=load("radarr/tag.json"))


def mock_sonarr():
    respx.get("http://sonarr/api/v3/series").respond(json=load("sonarr/series.json"))
    respx.get("http://sonarr/api/v3/qualityprofile").respond(json=load("sonarr/qualityprofile.json"))
    respx.get("http://sonarr/api/v3/tag").respond(json=load("sonarr/tag.json"))
    respx.get("http://sonarr/api/v3/episode", params={"seriesId": 5}).respond(
        json=load("sonarr/episode_1.json")
    )
    respx.get("http://sonarr/api/v3/episodefile", params={"seriesId": 5}).respond(
        json=load("sonarr/episodefile_1.json")
    )


@respx.mock
def test_radarr_movie_mapping():
    mock_radarr()
    snap = read_instance(RADARR, make_client(RADARR))
    assert snap.ok and snap.error is None
    assert snap.tags == {1: "4k", 2: "kids"}
    assert snap.profiles == {1: Res.R480, 2: Res.R1080, 3: Res.R2160}
    by_id = {i.arr_id: i for i in snap.items}
    m = by_id[11]
    assert (m.kind, m.title, m.year, m.tmdb, m.imdb, m.tvdb) == (
        "movie", "Blade Runner", 1982, 78, "tt0083658", None)
    assert m.has_file and m.monitored
    assert m.resolution == Res.R1080 and isinstance(m.resolution, int)
    assert m.edition == "Director's Cut"
    assert m.file_size == 12345678901
    assert m.cutoff_resolution == int(Res.R1080)
    assert m.quality_profile_id == 2 and m.tag_ids == [1, 2]
    assert m.poster_url == "https://img.example/poster.jpg"
    assert m.title_slug == "78"
    w = by_id[12]
    assert not w.has_file and w.resolution is None and w.edition is None
    assert w.file_size is None and w.poster_url is None
    assert w.cutoff_resolution == int(Res.R2160)


@respx.mock
def test_sonarr_series_mapping_does_not_read_episodes():
    mock_sonarr()
    snap = read_instance(SONARR, make_client(SONARR))
    assert snap.ok
    by_id = {i.arr_id: i for i in snap.items}
    s = by_id[5]
    assert (s.kind, s.tvdb, s.tmdb, s.imdb, s.title_slug) == (
        "series", 371980, 95396, "tt11280740", "severance")
    assert s.has_file and s.file_size == 1500000000
    assert s.poster_url == "https://img.example/sev.jpg"
    assert s.cutoff_resolution == int(Res.R1080)
    assert not by_id[6].has_file
    assert not any("/episode" in str(c.request.url) for c in respx.calls)


@respx.mock
def test_sonarr_episode_join():
    mock_sonarr()
    eps = {e.episode_arr_id: e for e in read_series_episodes(make_client(SONARR), 5)[0]}
    a, b = eps[501], eps[502]
    assert a.resolution == int(Res.R1080) and a.file_size == 1500000000
    assert a.episode_file_id == 9001 and a.season == 1 and a.episode == 1
    assert a.air_date_utc == datetime(2022, 2, 18, 2, 0, tzinfo=UTC)
    assert a.tvdb_id == 8001 and a.has_file
    assert b.resolution is None and b.file_size is None
    assert b.episode_file_id is None and not b.has_file


@respx.mock
def test_instance_failure_is_reported_not_raised(caplog):
    respx.get("http://radarr/api/v3/qualityprofile").respond(500, text=f"oops apikey={KEY}")
    snap = read_instance(RADARR, make_client(RADARR))
    assert not snap.ok and snap.items == []
    assert snap.error and KEY not in snap.error
    assert KEY not in caplog.text


@respx.mock
def test_unreachable_is_reported():
    respx.get("http://radarr/api/v3/qualityprofile").mock(
        side_effect=httpx.ConnectError(f"cannot reach, key {KEY}")
    )
    snap = read_instance(RADARR, make_client(RADARR))
    assert not snap.ok and KEY not in (snap.error or "")


def _item(arr_id, **kw):
    return TrackedItemData(arr_id=arr_id, kind="movie", title=f"T{arr_id}", **kw)


def _rows(db, inst):
    q = select(TrackedItem).where(TrackedItem.instance_id == inst)
    return {r.arr_id: r for r in db.scalars(q)}


def test_persist_upserts_and_deletes_vanished_only_on_success(db):
    persist_snapshot(db, InstanceSnapshot("r1", True, None, [_item(1, tag_ids=[1]), _item(2)]), 1)
    persist_snapshot(db, InstanceSnapshot("other", True, None, [_item(1)]), 1)
    rows = _rows(db, "r1")
    assert set(rows) == {1, 2} and rows[1].tag_ids == [1] and rows[1].seen_scan_id == 1

    # failed snapshot: nothing deleted or changed
    persist_snapshot(db, InstanceSnapshot("r1", False, "boom"), 2)
    assert set(_rows(db, "r1")) == {1, 2}

    # successful snapshot: update 1 (tags changed), drop 2, add 3
    persist_snapshot(
        db, InstanceSnapshot("r1", True, None, [_item(1, tag_ids=[7], title_slug="s"), _item(3)]), 3
    )
    rows = _rows(db, "r1")
    assert set(rows) == {1, 3}
    assert rows[1].tag_ids == [7] and rows[1].title_slug == "s" and rows[1].seen_scan_id == 3
    assert set(_rows(db, "other")) == {1}


@respx.mock
def test_persist_episodes_replaces_series_rows(db):
    mock_sonarr()
    eps, _ = read_series_episodes(make_client(SONARR), 5)
    persist_episodes(db, "s1", 5, eps)
    persist_episodes(db, "s1", 5, eps[:1])
    rows = db.scalars(select(TrackedEpisode)).all()
    assert [r.episode_arr_id for r in rows] == [501]
    assert rows[0].air_date_utc == datetime(2022, 2, 18, 2, 0, tzinfo=UTC)
    assert rows[0].resolution == int(Res.R1080)


@respx.mock
def test_malformed_entries_are_skipped_not_fatal(db, caplog):
    movies = load("radarr/movie.json")
    bad_images = dict(movies[0], id=23, images=["oops"])
    bad_21 = dict(movies[0], id=21, tags=7)
    no_id = {"title": "No Id"}
    bad_tags = dict(movies[1], id=22, tags=5)
    respx.get("http://radarr/api/v3/movie").respond(json=movies + [bad_images, bad_21, no_id, bad_tags])
    profiles = load("radarr/qualityprofile.json") + [{"name": "no id"}, "junk"]
    respx.get("http://radarr/api/v3/qualityprofile").respond(json=profiles)
    respx.get("http://radarr/api/v3/tag").respond(json=load("radarr/tag.json") + [{"id": 9}])
    persist_snapshot(db, InstanceSnapshot("r1", True, None, [_item(21), _item(11)]), 1)

    snap = read_instance(RADARR, make_client(RADARR))
    assert snap.ok
    assert {i.arr_id for i in snap.items} == {11, 12, 23}
    assert snap.skipped_ids == {21, 22}
    assert snap.tags == {1: "4k", 2: "kids"}
    assert set(snap.profiles) == {1, 2, 3}
    assert KEY not in caplog.text

    persist_snapshot(db, snap, 2)
    assert set(_rows(db, "r1")) == {11, 12, 21, 23}  # skipped id 21 keeps its old row


@respx.mock
def test_malformed_episodes_are_skipped(db):
    mock_sonarr()
    eps_json = load("sonarr/episode_1.json") + [
        {"id": 503, "seriesId": 5, "title": "no numbers"},
        {"seriesId": 5, "seasonNumber": 1, "episodeNumber": 3},
        {"id": 504, "seriesId": 5, "seasonNumber": 1, "episodeNumber": 4, "title": "x",
         "airDateUtc": 12, "hasFile": True, "episodeFileId": 777},
    ]
    respx.get("http://sonarr/api/v3/episode", params={"seriesId": 5}).respond(json=eps_json)
    respx.get("http://sonarr/api/v3/episodefile", params={"seriesId": 5}).respond(
        json=load("sonarr/episodefile_1.json") + [{"size": 1}, "junk"]
    )
    persist_episodes(db, "s1", 5, [
        TrackedEpisodeData(503, 5, 1, 9, "old"), TrackedEpisodeData(999, 5, 1, 8, "gone")])
    eps, skipped = read_series_episodes(make_client(SONARR), 5)
    assert {e.episode_arr_id for e in eps} == {501, 502, 504}
    assert skipped == {503}
    persist_episodes(db, "s1", 5, eps, keep_ids=skipped)
    ids = {r.episode_arr_id for r in db.scalars(select(TrackedEpisode))}
    assert ids == {501, 502, 504, 503}
