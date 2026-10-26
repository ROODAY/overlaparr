from datetime import UTC, datetime, timedelta

from overlaparr.arr.snapshot import TrackedEpisodeData
from overlaparr.db.models import IndexEpisode, IndexItem, TrackedItem
from overlaparr.domain.guids import ExternalIds
from overlaparr.domain.quality import Res
from overlaparr.matching.tv import ShowIndex, match_episodes, match_series_shows

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
PAST = NOW - timedelta(days=30)
ONLINE = {"s1": "online", "s2": "online"}


def series(arr_id=5, tvdb=100, tmdb=None, imdb=None, kind="series"):
    return TrackedItem(
        instance_id="sonarr", arr_id=arr_id, kind=kind, title="Show", tvdb=tvdb, tmdb=tmdb,
        imdb=imdb, monitored=True, tag_ids=[], seen_scan_id=1,
    )


def show(server="s1", tvdb=None, tmdb=None, imdb=None, title="Show", kind="show"):
    return IndexItem(
        server_id=server, kind=kind, rating_key=f"{server}-{tvdb}-{tmdb}-{imdb}-{title}",
        title=title, tvdb=tvdb, tmdb=tmdb, imdb=imdb,
    )


def ep(eid, season=1, number=None, tvdb_id=None, monitored=True, has_file=False, res=None,
       air=PAST):
    return TrackedEpisodeData(
        episode_arr_id=eid, series_arr_id=5, season=season,
        episode=number if number is not None else eid, title=f"E{eid}", tvdb_id=tvdb_id,
        monitored=monitored, has_file=has_file,
        resolution=int(res) if res is not None else None, air_date_utc=air,
    )


def sep(season=1, number=1, tvdb_episode_id=None, res=Res.R1080, size=100, server="s1",
        hdr=False, codec="h264"):
    return IndexEpisode(
        server_id=server, show_rating_key="rk", season=season, episode=number,
        tvdb_episode_id=tvdb_episode_id, resolution=int(res) if res is not None else None,
        hdr=hdr, codec=codec, size=size,
    )


def run(episodes, server_eps, status=ONLINE, cutoff=Res.R1080, s=None):
    return match_episodes(s or series(), episodes, server_eps, status, cutoff, NOW)


def by_type(findings):
    return {f.type: f for f in findings}


# --- show-level matching -------------------------------------------------------


def test_show_priority_tvdb_then_tmdb_then_imdb():
    index = ShowIndex.build([
        show("s1", tvdb=100, title="a"),
        show("s1", tmdb=7, title="b"),
        show("s2", tmdb=7, title="c"),
        show("s2", imdb="tt1", title="d"),
        show("s3", imdb="tt1", title="e"),
        show("s4", tvdb=100, kind="movie", title="m"),
    ])
    hits = index.lookup(ExternalIds(tvdb=100, tmdb=7, imdb="tt1"))
    assert {k: v.title for k, v in hits.items()} == {"s1": "a", "s2": "c", "s3": "e"}


def test_show_index_excludes_servers():
    index = ShowIndex.build([show("s1", tvdb=100), show("s2", tvdb=100)], exclude_servers={"s1"})
    assert set(index.lookup(ExternalIds(tvdb=100))) == {"s2"}


def test_match_series_shows():
    index = ShowIndex.build([show("s1", tvdb=100), show("s2", imdb="tt9")])
    result = match_series_shows(
        [series(arr_id=5, tvdb=100), series(arr_id=6, tvdb=None, imdb="tt9"),
         series(arr_id=7, tvdb=999), series(arr_id=8, tvdb=100, kind="movie")],
        index,
    )
    assert set(result) == {("sonarr", 5), ("sonarr", 6)}
    assert set(result[("sonarr", 5)]) == {"s1"}
    assert set(result[("sonarr", 6)]) == {"s2"}


# --- episode-level matching ----------------------------------------------------


def test_episode_id_match():
    [f] = run([ep(1, number=1, tvdb_id=501)], {"s1": [sep(season=3, number=9, tvdb_episode_id=501)]})
    assert (f.instance_id, f.arr_id, f.kind, f.type) == ("sonarr", 5, "series", "wanted_elsewhere")
    assert f.relevant_episode_ids == [1]
    [c] = f.copies
    assert c.server_id == "s1" and c.episode_arr_id == 1 and c.matched_by == "id"
    assert c.resolution == Res.R1080 and c.codec == "h264" and c.size == 100
    assert c.quality == "same" and c.edition_cmp == "unknown" and not c.stale
    assert f.coverage == "full"


def test_number_fallback_flagged():
    [f] = run([ep(1, number=2, tvdb_id=None)], {"s1": [sep(number=2, tvdb_episode_id=None)]})
    [c] = f.copies
    assert c.matched_by == "number" and c.episode_arr_id == 1
    # tracked id present but server episode lacks it: number fallback
    [f] = run([ep(1, number=2, tvdb_id=501)], {"s1": [sep(number=2, tvdb_episode_id=None)]})
    assert f.copies[0].matched_by == "number"


def test_id_match_not_double_used_by_number():
    # server S1E2 carries ep 1's tvdb id (numbering differs); it must not also number-match ep 2
    episodes = [ep(1, number=1, tvdb_id=501), ep(2, number=2, tvdb_id=502)]
    [f] = run(episodes, {"s1": [sep(number=2, tvdb_episode_id=501)]})
    assert [(c.episode_arr_id, c.matched_by) for c in f.copies] == [(1, "id")]
    assert f.coverage == "partial"


def test_id_matched_episode_not_also_number_matched():
    episodes = [ep(1, number=1, tvdb_id=501)]
    [f] = run(episodes, {"s1": [sep(number=5, tvdb_episode_id=501), sep(number=1, size=1)]})
    assert [(c.episode_arr_id, c.matched_by) for c in f.copies] == [(1, "id")]


def test_unaired_and_undated_not_wanted():
    episodes = [
        ep(1, air=PAST),
        ep(2, air=NOW + timedelta(days=7)),
        ep(3, air=None),
        ep(4, air=NOW),
    ]
    [f] = run(episodes, {"s1": [sep(number=1), sep(number=2), sep(number=3), sep(number=4)]})
    assert f.relevant_episode_ids == [1, 4]
    assert sorted(c.episode_arr_id for c in f.copies) == [1, 4]
    assert f.coverage == "full"


def test_unmonitored_missing_not_wanted():
    assert run([ep(1, monitored=False)], {"s1": [sep(number=1)]}) == []


def test_partial_counts():
    episodes = [ep(i) for i in range(1, 20)]
    [f] = run(episodes, {"s1": [sep(number=i) for i in range(1, 10)]})
    assert f.relevant_episode_ids == list(range(1, 20))
    assert len(f.covered_episode_ids) == 9 and len(f.relevant_episode_ids) == 19
    assert f.coverage == "partial"


def test_two_findings_when_mixed():
    episodes = [ep(1, has_file=True, res=Res.R720), ep(2), ep(3)]
    found = by_type(run(episodes, {"s1": [sep(number=1), sep(number=2)]}))
    assert set(found) == {"wanted_elsewhere", "on_disk"}
    wanted, disk = found["wanted_elsewhere"], found["on_disk"]
    assert wanted.relevant_episode_ids == [2, 3]
    assert [c.episode_arr_id for c in wanted.copies] == [2]
    assert wanted.coverage == "partial"
    assert disk.relevant_episode_ids == [1]
    assert [c.episode_arr_id for c in disk.copies] == [1]
    assert disk.copies[0].quality == "better"
    assert disk.coverage == "full"


def test_no_finding_without_copies():
    episodes = [ep(1, has_file=True), ep(2)]
    found = by_type(run(episodes, {"s1": [sep(number=2)]}))
    assert set(found) == {"wanted_elsewhere"}
    assert run(episodes, {}) == []


def test_specials_included():
    [f] = run([ep(1, season=0, number=1)], {"s1": [sep(season=0, number=1)]})
    assert f.relevant_episode_ids == [1] and f.copies[0].episode_arr_id == 1


def test_per_episode_quality():
    episodes = [ep(1, has_file=True, res=Res.R1080)]
    [f] = run(episodes, {"s1": [sep(number=1, res=Res.R2160)], "s2": [sep(number=1, res=Res.R720)]})
    assert f.type == "on_disk"
    assert {c.server_id: c.quality for c in f.copies} == {"s1": "better", "s2": "worse"}


def test_wanted_quality_vs_cutoff_and_unknown():
    [f] = run([ep(1)], {"s1": [sep(number=1, res=Res.R720)], "s2": [sep(number=1, res=None)]},
              cutoff=Res.R1080)
    assert {c.server_id: c.quality for c in f.copies} == {"s1": "worse", "s2": "unknown"}
    [f] = run([ep(1)], {"s1": [sep(number=1)]}, cutoff=None)
    assert f.copies[0].quality == "unknown"


def test_duplicate_numbers_use_best():
    eps = [sep(number=1, res=Res.R720, size=900), sep(number=1, res=Res.R1080, size=10),
           sep(number=1, res=Res.R1080, size=50)]
    [f] = run([ep(1)], {"s1": eps})
    [c] = f.copies
    assert c.resolution == Res.R1080 and c.size == 50


def test_stale_per_copy():
    [f] = run([ep(1)], {"s1": [sep(number=1)], "s2": [sep(number=1)], "s3": [sep(number=1)]},
              status={"s1": "online", "s2": "degraded", "s3": "offline"})
    assert {c.server_id: c.stale for c in f.copies} == {"s1": False, "s2": False, "s3": True}


def test_naive_now_rejected():
    import pytest

    with pytest.raises(ValueError):
        match_episodes(series(), [ep(1)], {"s1": [sep(number=1)]}, ONLINE, None, datetime(2026, 1, 1))
