import json

import httpx
import pytest
import respx

from overlaparr.arr.client import ArrClient, ArrError, DryRunViolation
from overlaparr.arr.radarr import RadarrClient
from overlaparr.arr.snapshot import make_client
from overlaparr.arr.sonarr import SonarrClient
from overlaparr.config.schema import InstanceConfig

KEY = "supersecretapikey123"
R = "http://radarr:7878"
S = "http://sonarr:8989"


def radarr(**kw):
    return RadarrClient(R, KEY, **kw)


def sonarr(**kw):
    return SonarrClient(S, KEY, **kw)


def body(route):
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_radarr_set_monitored():
    route = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    radarr().set_movies_monitored([1, 2], False)
    assert body(route) == {"movieIds": [1, 2], "monitored": False}
    assert route.calls.last.request.headers["X-Api-Key"] == KEY


@respx.mock
def test_radarr_delete_movie_file():
    route = respx.delete(f"{R}/api/v3/moviefile/7").respond(200)
    radarr().delete_movie_file(7)
    assert route.called


@respx.mock
@pytest.mark.parametrize("flag", [True, False])
def test_radarr_delete_movie_query(flag):
    route = respx.delete(f"{R}/api/v3/movie/5").respond(200)
    radarr().delete_movie(5, flag)
    url = route.calls.last.request.url
    assert url.params["deleteFiles"] == "true"
    assert url.params["addImportExclusion"] == ("true" if flag else "false")


@respx.mock
def test_radarr_exclusions():
    add = respx.post(f"{R}/api/v3/exclusions").respond(json={"id": 42})
    assert radarr().add_exclusion(603, "The Matrix", 1999) == 42
    assert body(add) == {"tmdbId": 603, "movieTitle": "The Matrix", "movieYear": 1999}
    rm = respx.delete(f"{R}/api/v3/exclusions/42").respond(200)
    radarr().remove_exclusion(42)
    assert rm.called
    respx.get(f"{R}/api/v3/exclusions").respond(
        json=[{"id": 1, "tmdbId": 10}, {"id": 2, "tmdbId": 603}]
    )
    assert radarr().find_exclusion(603) == 2
    assert radarr().find_exclusion(999) is None


@respx.mock
def test_ensure_tag_existing_case_insensitive_no_post():
    respx.get(f"{R}/api/v3/tag").respond(json=[{"id": 3, "label": "Overlap"}])
    post = respx.post(f"{R}/api/v3/tag").respond(json={"id": 9})
    assert radarr().ensure_tag("overlap") == 3
    assert not post.called


@respx.mock
def test_ensure_tag_creates():
    respx.get(f"{R}/api/v3/tag").respond(json=[])
    post = respx.post(f"{R}/api/v3/tag").respond(json={"id": 9, "label": "x"})
    assert radarr().ensure_tag("x") == 9
    assert body(post) == {"label": "x"}


@respx.mock
@pytest.mark.parametrize("mode", ["add", "remove"])
def test_radarr_edit_tags_and_profile_and_search(mode):
    route = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    radarr().edit_tags([1], [4], mode)
    assert body(route) == {"movieIds": [1], "tags": [4], "applyTags": mode}
    radarr().set_quality_profile([1, 2], 6)
    assert body(route) == {"movieIds": [1, 2], "qualityProfileId": 6}
    cmd = respx.post(f"{R}/api/v3/command").respond(json={"id": 1})
    radarr().search([1, 2])
    assert body(cmd) == {"name": "MoviesSearch", "movieIds": [1, 2]}


@respx.mock
def test_radarr_get_movie():
    respx.get(f"{R}/api/v3/movie/3").respond(json={"id": 3})
    assert radarr().get_movie(3) == {"id": 3}


@respx.mock
def test_sonarr_monitor_and_editor():
    ep = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    sonarr().set_episodes_monitored([1, 2], True)
    assert body(ep) == {"episodeIds": [1, 2], "monitored": True}
    ed = respx.put(f"{S}/api/v3/series/editor").respond(json=[])
    sonarr().set_series_monitored([5], False)
    assert body(ed) == {"seriesIds": [5], "monitored": False}
    sonarr().edit_tags([5], [2], "add")
    assert body(ed) == {"seriesIds": [5], "tags": [2], "applyTags": "add"}
    sonarr().set_quality_profile([5], 8)
    assert body(ed) == {"seriesIds": [5], "qualityProfileId": 8}


@respx.mock
def test_sonarr_delete_episode_files_json_body():
    route = respx.delete(f"{S}/api/v3/episodefile/bulk").respond(200)
    sonarr().delete_episode_files([10, 11])
    assert body(route) == {"episodeFileIds": [10, 11]}


@respx.mock
@pytest.mark.parametrize("flag", [True, False])
def test_sonarr_delete_series_query(flag):
    route = respx.delete(f"{S}/api/v3/series/4").respond(200)
    sonarr().delete_series(4, flag)
    p = route.calls.last.request.url.params
    assert p["deleteFiles"] == "true"
    assert p["addImportListExclusion"] == ("true" if flag else "false")


@respx.mock
def test_sonarr_exclusions():
    add = respx.post(f"{S}/api/v3/importlistexclusion").respond(json={"id": 11})
    assert sonarr().add_exclusion(81189, "Breaking Bad") == 11
    assert body(add) == {"tvdbId": 81189, "title": "Breaking Bad"}
    rm = respx.delete(f"{S}/api/v3/importlistexclusion/11").respond(200)
    sonarr().remove_exclusion(11)
    assert rm.called
    respx.get(f"{S}/api/v3/importlistexclusion").respond(json=[{"id": 11, "tvdbId": 81189}])
    assert sonarr().find_exclusion(81189) == 11
    assert sonarr().find_exclusion(1) is None


@respx.mock
def test_sonarr_search_and_get_episodes():
    cmd = respx.post(f"{S}/api/v3/command").respond(json={"id": 1})
    sonarr().search_episodes([3, 4])
    assert body(cmd) == {"name": "EpisodeSearch", "episodeIds": [3, 4]}
    get = respx.get(f"{S}/api/v3/episode").respond(json=[{"id": 1}, {"id": 2}])
    assert sonarr().get_episodes([1, 2]) == [{"id": 1}, {"id": 2}]
    assert get.calls.last.request.url.params.get_list("episodeIds") == ["1", "2"]


@respx.mock
def test_empty_id_lists_make_no_http_call():
    r, s = radarr(), sonarr()
    r.set_movies_monitored([], True)
    r.edit_tags([], [1], "add")
    r.set_quality_profile([], 1)
    r.search([])
    s.set_episodes_monitored([], True)
    s.set_series_monitored([], True)
    s.delete_episode_files([])
    s.edit_tags([], [1], "add")
    s.set_quality_profile([], 1)
    s.search_episodes([])
    assert s.get_episodes([]) == []
    assert respx.calls.call_count == 0


@respx.mock
def test_dry_run_client_refuses_writes():
    r, s = radarr(dry_run=True), sonarr(dry_run=True)
    calls = [
        lambda: r.set_movies_monitored([1], True),
        lambda: r.set_movies_monitored([], True),
        lambda: r.delete_movie_file(1),
        lambda: r.delete_movie(1, True),
        lambda: r.add_exclusion(1, "t", 2000),
        lambda: r.remove_exclusion(1),
        lambda: r.edit_tags([1], [1], "add"),
        lambda: r.set_quality_profile([1], 1),
        lambda: r.search([1]),
        lambda: s.set_episodes_monitored([1], True),
        lambda: s.set_series_monitored([1], True),
        lambda: s.delete_episode_files([1]),
        lambda: s.delete_series(1, False),
        lambda: s.add_exclusion(1, "t"),
        lambda: s.remove_exclusion(1),
        lambda: s.edit_tags([1], [1], "remove"),
        lambda: s.set_quality_profile([1], 1),
        lambda: s.search_episodes([1]),
        lambda: r.post("/x"),
        lambda: r.put("/x"),
        lambda: r.delete("/x"),
    ]
    for c in calls:
        with pytest.raises(DryRunViolation):
            c()
    assert respx.calls.call_count == 0


@respx.mock
def test_dry_run_client_still_reads():
    respx.get(f"{R}/api/v3/movie/3").respond(json={"id": 3})
    assert radarr(dry_run=True).get_movie(3) == {"id": 3}


def test_make_client_dry_run_passthrough():
    inst = InstanceConfig(id="a", name="a", type="radarr", url="http://h:1", api_key=KEY)
    assert make_client(inst).dry_run is False
    assert make_client(inst, dry_run=True).dry_run is True


@respx.mock
def test_write_error_hides_api_key():
    respx.put("http://x/api/v3/q").respond(500)
    with pytest.raises(ArrError) as ei:
        ArrClient("http://x", KEY).put("/api/v3/q", {})
    assert ei.value.status == 500 and KEY not in str(ei.value)
    respx.post("http://x/api/v3/q").mock(side_effect=httpx.ConnectError(f"boom {KEY}"))
    with pytest.raises(ArrError) as ei:
        ArrClient("http://x", KEY).post("/api/v3/q", {})
    assert ei.value.status is None and KEY not in str(ei.value)
