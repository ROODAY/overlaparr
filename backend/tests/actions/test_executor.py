import pytest
import respx
from sqlalchemy import select

from overlaparr.actions.executor import ActionError, ActionRequest, execute
from overlaparr.arr.client import ArrError
from overlaparr.arr.radarr import RadarrClient
from overlaparr.config.schema import Rule
from overlaparr.db.models import ActionLog, Finding, FindingServerIgnore
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.logging_setup import register_secret
from tests.actions.helpers import (
    R,
    RADARR,
    S,
    SONARR,
    add_episodes,
    add_finding,
    body,
    episode,
    episode_file,
    make_cfg,
    movie,
    non_get_calls,
)


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


@pytest.fixture
def cfg():
    return make_cfg()


def series_finding(db):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance")
    add_episodes(db)
    return f


@respx.mock
def test_unmonitor_episodes_records_only_changed(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/episode").respond(
        json=[episode(501, True, 9001), episode(503, False, 9003)]
    )
    put = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor", scope="episodes",
                                         episode_ids=[501, 503]), "ui")
    assert row.result == "ok", row.message
    assert row.prior_state == {"monitored": {"501": True}}
    assert body(put) == {"episodeIds": [501], "monitored": False}
    assert row.undoable and row.scope["episode_ids"] == [501, 503]
    db.refresh(f)
    assert f.review_state == "actioned"


@respx.mock
def test_unmonitor_movie_already_unmonitored_no_write(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie(monitored=False))
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor"), "ui")
    assert row.result == "ok" and row.prior_state == {"monitored": {}}
    assert not row.undoable
    assert non_get_calls() == []
    db.refresh(f)
    assert f.review_state == "new"  # nothing changed in Radarr


@respx.mock
def test_unmonitor_series_item_uses_series_flag(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/series/5").respond(json={"id": 5, "title": "Severance",
                                                     "monitored": True})
    put = respx.put(f"{S}/api/v3/series/editor").respond(json=[])
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor"), "ui")
    assert row.result == "ok", row.message
    assert row.prior_state == {"monitored": {"5": True}}
    assert body(put) == {"seriesIds": [5], "monitored": False}


@pytest.mark.parametrize(
    "episode_ids",
    [[], [501, 999]],  # empty, or 999 which is not one of the finding's episodes
)
@respx.mock
def test_episode_scope_validation(db, cfg, episode_ids):
    f = series_finding(db)
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor", scope="episodes",
                                         episode_ids=episode_ids), "ui")
    assert row.result == "error"
    assert respx.calls.call_count == 0


@respx.mock
def test_episode_scope_rejected_for_movies(db, cfg):
    f = add_finding(db)
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor", scope="episodes",
                                         episode_ids=[1]), "ui")
    assert row.result == "error"


@respx.mock
def test_delete_files_mode_unmonitors_and_logs_paths(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/episode").respond(
        json=[episode(501, True, 9001), episode(503, False, 9003)]
    )
    respx.get(f"{S}/api/v3/episodefile").respond(
        json=[episode_file(9001, 1111), episode_file(9003, 3333)]
    )
    mon = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    dele = respx.delete(f"{S}/api/v3/episodefile/bulk").respond(200)
    row = execute(db, cfg, ActionRequest(f.id, "delete", scope="episodes",
                                         episode_ids=[501, 503]), "ui")
    assert row.result == "ok", row.message
    assert body(dele) == {"episodeFileIds": [9001, 9003]}
    assert body(mon) == {"episodeIds": [501], "monitored": False}
    assert row.prior_state == {
        "deleted": [
            {"file_id": 9001, "path": "/tv/Show/S01/9001.mkv", "size": 1111,
             "quality": "WEBDL-1080p"},
            {"file_id": 9003, "path": "/tv/Show/S01/9003.mkv", "size": 3333,
             "quality": "WEBDL-1080p"},
        ],
        "monitored": {"501": True},
    }
    assert row.undoable
    assert "cannot be restored" in row.message


@respx.mock
def test_delete_files_movie(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    mon = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    dele = respx.delete(f"{R}/api/v3/moviefile/77").respond(200)
    row = execute(db, cfg, ActionRequest(f.id, "delete"), "ui")
    assert row.result == "ok", row.message
    assert dele.called and body(mon) == {"movieIds": [1], "monitored": False}
    assert row.prior_state == {
        "deleted": [{"file_id": 77, "path": "/movies/The Matrix (1999)/matrix.mkv",
                     "size": 5000, "quality": "WEBDL-1080p"}],
        "monitored": {"1": True},
    }


@respx.mock
def test_delete_files_series_item_unmonitors_episodes_not_series(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/episode", params={"seriesId": 5}).respond(
        json=[episode(501, True, 9001), episode(502, True), episode(503, False, 9003)]
    )
    respx.get(f"{S}/api/v3/episodefile").respond(json=[episode_file(9001), episode_file(9003)])
    mon = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    dele = respx.delete(f"{S}/api/v3/episodefile/bulk").respond(200)
    row = execute(db, cfg, ActionRequest(f.id, "delete"), "ui")
    assert row.result == "ok", row.message
    assert body(dele) == {"episodeFileIds": [9001, 9003]}
    # Only episodes whose files were deleted: 502 (monitored, no file) keeps
    # grabbing, so a continuing series still picks up new episodes.
    assert body(mon) == {"episodeIds": [501], "monitored": False}
    assert row.prior_state["monitored"] == {"501": True}


@respx.mock
def test_delete_files_partial_failure_keeps_monitoring_undoable(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/episode").respond(
        json=[episode(501, True, 9001), episode(503, False, 9003)]
    )
    respx.get(f"{S}/api/v3/episodefile").respond(json=[episode_file(9001), episode_file(9003)])
    respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    respx.delete(f"{S}/api/v3/episodefile/bulk").respond(500)
    row = execute(db, cfg, ActionRequest(f.id, "delete", scope="episodes",
                                         episode_ids=[501, 503]), "ui")
    assert row.result == "error"
    assert not row.prior_state.get("deleted")
    assert row.prior_state["monitored"] == {"501": True}
    assert row.undoable
    db.refresh(f)
    assert f.review_state == "new"


@respx.mock
def test_delete_files_nothing_to_do_is_noop(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie(monitored=False, has_file=False))
    row = execute(db, cfg, ActionRequest(f.id, "delete"), "ui")
    assert row.result == "ok" and not row.undoable
    assert row.prior_state == {"deleted": [], "monitored": {}}
    assert non_get_calls() == []
    db.refresh(f)
    assert f.review_state == "new"


@respx.mock
def test_delete_files_without_monitoring_change_not_undoable(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie(monitored=False))
    respx.delete(f"{R}/api/v3/moviefile/77").respond(200)
    row = execute(db, cfg, ActionRequest(f.id, "delete"), "ui")
    assert row.result == "ok" and not row.undoable
    assert [d["file_id"] for d in row.prior_state["deleted"]] == [77]
    db.refresh(f)
    assert f.review_state == "actioned"


@respx.mock
def test_remove_item_not_undoable(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    dele = respx.delete(f"{R}/api/v3/movie/1").respond(200)
    row = execute(db, cfg, ActionRequest(f.id, "delete", delete_mode="remove_item",
                                         add_exclusion=True), "ui")
    assert row.result == "ok", row.message
    assert dele.calls.last.request.url.params["addImportExclusion"] == "true"
    assert not row.undoable
    assert row.prior_state == {"item": {"title": "The Matrix",
                                        "ids": {"tmdb": 603, "imdb": "tt0133093"},
                                        "path": "/movies/The Matrix (1999)", "size": 5000}}
    db.refresh(f)
    assert f.lifecycle == "removed" and f.review_state == "actioned"


@respx.mock
def test_remove_item_series(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/series/5").respond(json={
        "id": 5, "title": "Severance", "tvdbId": 371980, "tmdbId": 95396, "imdbId": "tt11280740",
        "path": "/tv/Severance", "statistics": {"sizeOnDisk": 900}})
    dele = respx.delete(f"{S}/api/v3/series/5").respond(200)
    row = execute(db, cfg, ActionRequest(f.id, "delete", delete_mode="remove_item"), "ui")
    assert row.result == "ok", row.message
    assert dele.calls.last.request.url.params["addImportListExclusion"] == "false"
    assert row.prior_state == {"item": {
        "title": "Severance", "ids": {"tvdb": 371980, "tmdb": 95396, "imdb": "tt11280740"},
        "path": "/tv/Severance", "size": 900}}


@respx.mock
def test_remove_item_rejected_for_episode_scope(db, cfg):
    f = series_finding(db)
    row = execute(db, cfg, ActionRequest(f.id, "delete", scope="episodes", episode_ids=[501],
                                         delete_mode="remove_item"), "ui")
    assert row.result == "error"
    assert "item" in row.message
    assert respx.calls.call_count == 0
    db.refresh(f)
    assert f.review_state == "new" and f.lifecycle == "active"


@respx.mock
def test_exclude_movie_adds_exclusion(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.get(f"{R}/api/v3/exclusions").respond(json=[])
    add = respx.post(f"{R}/api/v3/exclusions").respond(json={"id": 42})
    row = execute(db, cfg, ActionRequest(f.id, "exclude"), "ui")
    assert row.result == "ok", row.message
    assert body(add) == {"tmdbId": 603, "movieTitle": "The Matrix", "movieYear": 1999}
    assert row.prior_state == {"exclusion_id": 42} and row.undoable


@respx.mock
def test_exclude_series_adds_exclusion(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/series/5").respond(json={"id": 5, "title": "Severance",
                                                     "tvdbId": 371980})
    respx.get(f"{S}/api/v3/importlistexclusion").respond(json=[])
    add = respx.post(f"{S}/api/v3/importlistexclusion").respond(json={"id": 8})
    row = execute(db, cfg, ActionRequest(f.id, "exclude"), "ui")
    assert row.result == "ok", row.message
    assert body(add) == {"tvdbId": 371980, "title": "Severance"}
    assert row.prior_state == {"exclusion_id": 8}


@respx.mock
def test_exclude_already_excluded_skips(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.get(f"{R}/api/v3/exclusions").respond(json=[{"id": 9, "tmdbId": 603}])
    row = execute(db, cfg, ActionRequest(f.id, "exclude"), "ui")
    assert row.result == "ok" and row.message == "already excluded"
    assert not row.undoable and non_get_calls() == []
    db.refresh(f)
    assert f.review_state == "new"


@respx.mock
def test_tag_adds_dupe_tag(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/tag").respond(json=[{"id": 4, "label": "dupe-elsewhere"}])
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie(tags=[1]))
    put = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    row = execute(db, cfg, ActionRequest(f.id, "tag"), "ui")
    assert row.result == "ok", row.message
    assert body(put) == {"movieIds": [1], "tags": [4], "applyTags": "add"}
    assert row.prior_state == {"tags_added": [4]} and row.undoable


@respx.mock
def test_tag_already_present_records_nothing(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/tag").respond(json=[{"id": 4, "label": "dupe-elsewhere"}])
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie(tags=[4]))
    row = execute(db, cfg, ActionRequest(f.id, "tag"), "ui")
    assert row.result == "ok" and row.prior_state == {} and not row.undoable
    assert non_get_calls() == []
    db.refresh(f)
    assert f.review_state == "new"


@respx.mock
def test_tag_write_failure_records_no_tags_added(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/tag").respond(json=[{"id": 4, "label": "dupe-elsewhere"}])
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.put(f"{R}/api/v3/movie/editor").respond(500)
    row = execute(db, cfg, ActionRequest(f.id, "tag"), "ui")
    assert row.result == "error" and "tags_added" not in row.prior_state
    assert not row.undoable


@respx.mock
def test_unmonitor_write_failure_records_nothing(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.put(f"{R}/api/v3/movie/editor").respond(500)
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor"), "ui")
    assert row.result == "error" and not row.prior_state.get("monitored")
    assert not row.undoable


@respx.mock
def test_dry_run_zero_writes(db, cfg):
    f = series_finding(db)
    respx.get(f"{S}/api/v3/episode").respond(
        json=[episode(501, True, 9001), episode(503, False, 9003)]
    )
    respx.get(f"{S}/api/v3/episodefile").respond(json=[episode_file(9001), episode_file(9003)])
    respx.put(url__startswith=S).respond(200)
    respx.delete(url__startswith=S).respond(200)
    respx.post(url__startswith=S).respond(200)
    rule = Rule(id="ru1", name="Delete dupes", action="delete")
    row = execute(db, cfg, ActionRequest(f.id, "delete", scope="episodes",
                                         episode_ids=[501, 503]),
                  "auto", rule=rule, scan_id=3, dry_run=True)
    assert row.result == "dry_run", row.message
    assert non_get_calls() == []
    assert row.prior_state["monitored"] == {"501": True}
    assert [d["file_id"] for d in row.prior_state["deleted"]] == [9001, 9003]
    assert row.rule_id == "ru1" and row.scan_run_id == 3 and row.actor == "auto"
    assert not row.undoable
    db.refresh(f)
    assert f.review_state == "new"


@respx.mock
def test_dry_run_tag_does_not_create_tag(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/tag").respond(json=[])
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.post(url__startswith=R).respond(json={"id": 1})
    row = execute(db, cfg, ActionRequest(f.id, "tag"), "ui", dry_run=True)
    assert row.result == "dry_run", row.message
    assert non_get_calls() == []


def test_dry_run_review_action_changes_nothing(db, cfg):
    f = add_finding(db)
    row = execute(db, cfg, ActionRequest(f.id, "ignore"), "ui", dry_run=True)
    assert row.result == "dry_run" and row.prior_state == {"review_state": "new"}
    db.refresh(f)
    assert f.review_state == "new"


def test_dry_run_builds_dry_run_client(db, cfg):
    f = add_finding(db)
    seen = []

    class Boom(Exception):
        pass

    def client_for(inst, dry_run=False):
        seen.append(dry_run)
        raise Boom

    with pytest.raises(Boom):
        execute(db, cfg, ActionRequest(f.id, "unmonitor"), "ui", dry_run=True,
                client_for=client_for)
    assert seen == [True]


@respx.mock
def test_error_redacted_and_logged(db, cfg, monkeypatch):
    register_secret("hunter2-token-xyz")
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())

    def boom(self, ids, monitored):
        raise ArrError(500, "failed with token hunter2-token-xyz")

    monkeypatch.setattr(RadarrClient, "set_movies_monitored", boom)
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor"), "ui")
    assert row.result == "error"
    assert "hunter2-token-xyz" not in row.message and "failed with token" in row.message
    assert not row.undoable
    db.refresh(f)
    assert f.review_state == "new"
    assert db.scalars(select(ActionLog)).all() == [row]


@respx.mock
def test_http_error_logged(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.put(f"{R}/api/v3/movie/editor").respond(500)
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor"), "ui")
    assert row.result == "error" and "500" in row.message
    assert "radarrkey123" not in row.message


def test_missing_instance_logged_as_error(db):
    f = add_finding(db, instance="gone")
    row = execute(db, make_cfg(RADARR, SONARR), ActionRequest(f.id, "unmonitor"), "ui")
    assert row.result == "error" and row.message == "Instance no longer configured"
    assert row.instance_name == "gone"


def test_unknown_finding_raises(db, cfg):
    with pytest.raises(ActionError):
        execute(db, cfg, ActionRequest(999, "ignore"), "ui")


@respx.mock
def test_log_keeps_name_snapshot(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    rule = Rule(id="ru1", name="Unmonitor dupes")
    row = execute(db, cfg, ActionRequest(f.id, "unmonitor"), "auto", rule=rule)
    # Later renames of the instance/item must not rewrite history.
    cfg.instances[0].name = "Renamed Radarr"
    db.expire_all()
    row = db.get(ActionLog, row.id)
    assert row.instance_name == "My Radarr"
    assert row.item_title == "The Matrix"
    assert row.rule_name == "Unmonitor dupes" and row.rule_id == "ru1"
    assert row.instance_id == "r1" and row.arr_id == 1 and row.finding_id == f.id


@pytest.mark.parametrize(
    ("action", "start", "end"),
    [
        ("ignore", "new", "ignored"),
        ("unignore", "ignored", "new"),
        ("mark_reviewed", "new", "reviewed"),
        ("mark_new", "reviewed", "new"),
    ],
)
def test_review_actions(db, cfg, action, start, end):
    f = add_finding(db, review_state=start)
    row = execute(db, cfg, ActionRequest(f.id, action), "ui")
    assert row.result == "ok" and row.prior_state == {"review_state": start}
    assert row.undoable
    db.refresh(f)
    assert f.review_state == end


def test_ignore_server(db, cfg):
    f = add_finding(db)
    row = execute(db, cfg, ActionRequest(f.id, "ignore_server", server_id="srv1"), "ui")
    assert row.result == "ok" and row.undoable
    ig = db.scalars(select(FindingServerIgnore)).one()
    assert (ig.finding_id, ig.server_id) == (f.id, "srv1")
    assert db.get(Finding, f.id).review_state == "new"


def test_ignore_server_requires_server(db, cfg):
    f = add_finding(db)
    row = execute(db, cfg, ActionRequest(f.id, "ignore_server"), "ui")
    assert row.result == "error"
