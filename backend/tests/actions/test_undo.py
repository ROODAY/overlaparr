import json

import pytest
import respx
from sqlalchemy import select

from overlaparr.actions.executor import ActionRequest, execute
from overlaparr.actions.undo import UndoError, undo
from overlaparr.db.models import ActionLog, Finding, FindingServerIgnore
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from tests.actions.helpers import (
    R,
    RADARR,
    S,
    add_episodes,
    add_finding,
    body,
    episode,
    episode_file,
    make_cfg,
    movie,
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


def log_row(db, f, action, prior, instance="s1", instance_name="My Sonarr", actor="ui",
            scope=None, undoable=True, result="ok"):
    row = ActionLog(actor=actor, instance_id=instance, instance_name=instance_name,
                    finding_id=f.id, arr_id=f.arr_id, item_title="X", action=action,
                    scope=scope or {"scope": "item"}, prior_state=prior, result=result,
                    undoable=undoable)
    db.add(row)
    db.commit()
    return row


@respx.mock
def test_undo_restores_exact_prior_monitoring(db, cfg):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance")
    add_episodes(db, eps=((501, True, None), (502, False, None), (503, True, None)))
    orig = log_row(db, f, "unmonitor", {"monitored": {"501": True, "502": False, "503": True}},
                   scope={"scope": "episodes", "episode_ids": [501, 502, 503]})
    put = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    row = undo(db, cfg, orig.id)
    assert row.result == "ok", row.message
    bodies = sorted(
        (b["monitored"], sorted(b["episodeIds"]))
        for b in (json.loads(c.request.content) for c in put.calls)
    )
    assert bodies == [(False, [502]), (True, [501, 503])]
    assert row.undo_of_id == orig.id and not row.undoable
    db.refresh(orig)
    assert orig.undone_at is not None


@respx.mock
def test_undo_roundtrip_episode_unmonitor(db, cfg):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance")
    add_episodes(db)
    respx.get(f"{S}/api/v3/episode").respond(
        json=[episode(501, True, 9001), episode(503, False, 9003)]
    )
    put = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    done = execute(db, cfg, ActionRequest(f.id, "unmonitor", scope="episodes",
                                          episode_ids=[501, 503]), "ui")
    row = undo(db, cfg, done.id)
    assert row.result == "ok"
    assert body(put) == {"episodeIds": [501], "monitored": True}


@respx.mock
def test_undo_movie_unmonitor(db, cfg):
    f = add_finding(db)
    orig = log_row(db, f, "unmonitor", {"monitored": {"1": True}}, instance="r1",
                   instance_name="My Radarr")
    put = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    assert undo(db, cfg, orig.id).result == "ok"
    assert body(put) == {"movieIds": [1], "monitored": True}


@respx.mock
def test_undo_series_unmonitor_uses_series_flag(db, cfg):
    f = add_finding(db, instance="s1", arr_id=5, kind="series")
    orig = log_row(db, f, "unmonitor", {"monitored": {"5": True}})
    put = respx.put(f"{S}/api/v3/series/editor").respond(json=[])
    assert undo(db, cfg, orig.id).result == "ok"
    assert body(put) == {"seriesIds": [5], "monitored": True}


@respx.mock
def test_undo_delete_files_restores_monitoring_only(db, cfg):
    f = add_finding(db, instance="s1", arr_id=5, kind="series")
    orig = log_row(db, f, "delete", {
        "deleted": [{"file_id": 9001, "path": None, "size": 1, "quality": None}],
        "monitored": {"501": True}}, scope={"scope": "item", "delete_mode": "files"})
    put = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    row = undo(db, cfg, orig.id)
    assert row.result == "ok"
    assert body(put) == {"episodeIds": [501], "monitored": True}
    assert "cannot be restored" in row.message


@respx.mock
def test_exclusion_undo_removes_it(db, cfg):
    f = add_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.get(f"{R}/api/v3/exclusions").respond(json=[])
    respx.post(f"{R}/api/v3/exclusions").respond(json={"id": 42})
    done = execute(db, cfg, ActionRequest(f.id, "exclude"), "ui")
    rm = respx.delete(f"{R}/api/v3/exclusions/42").respond(200)
    row = undo(db, cfg, done.id)
    assert row.result == "ok" and rm.called


@respx.mock
def test_tag_undo_removes_only_added(db, cfg):
    f = add_finding(db)
    orig = log_row(db, f, "tag", {"tags_added": [4]}, instance="r1", instance_name="My Radarr")
    put = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    assert undo(db, cfg, orig.id).result == "ok"
    assert body(put) == {"movieIds": [1], "tags": [4], "applyTags": "remove"}


def test_review_undo_restores_prior_state(db, cfg):
    f = add_finding(db, review_state="reviewed")
    done = execute(db, cfg, ActionRequest(f.id, "ignore"), "ui")
    row = undo(db, cfg, done.id)
    assert row.result == "ok"
    db.refresh(f)
    assert f.review_state == "reviewed"


def test_ignore_server_undo_removes_row(db, cfg):
    f = add_finding(db)
    done = execute(db, cfg, ActionRequest(f.id, "ignore_server", server_id="srv1"), "ui")
    undo(db, cfg, done.id)
    assert db.scalars(select(FindingServerIgnore)).all() == []


def test_undo_auto_action_sets_hold(db, cfg):
    f = add_finding(db)
    done = execute(db, cfg, ActionRequest(f.id, "ignore"), "auto")
    row = undo(db, cfg, done.id)
    assert row.result == "ok" and row.actor == "ui"
    db.refresh(f)
    assert f.automation_hold is True


def test_undo_ui_action_does_not_set_hold(db, cfg):
    f = add_finding(db)
    done = execute(db, cfg, ActionRequest(f.id, "ignore"), "ui")
    undo(db, cfg, done.id)
    db.refresh(f)
    assert f.automation_hold is False


def test_undo_missing_instance_clear_error(db):
    f = add_finding(db, instance="s1", arr_id=5, kind="series")
    orig = log_row(db, f, "unmonitor", {"monitored": {"5": True}}, instance_name="Old Sonarr")
    with pytest.raises(UndoError, match=r"^Instance 'Old Sonarr' no longer configured$"):
        undo(db, make_cfg(RADARR), orig.id)
    db.refresh(orig)
    assert orig.undone_at is None


@pytest.mark.parametrize(
    "kw", [{"undoable": False}, {"result": "error"}, {"result": "dry_run"}]
)
def test_undo_rejects_non_undoable(db, cfg, kw):
    f = add_finding(db)
    orig = log_row(db, f, "ignore", {"review_state": "new"}, **kw)
    with pytest.raises(UndoError):
        undo(db, cfg, orig.id)


def test_undo_twice_rejected(db, cfg):
    f = add_finding(db)
    done = execute(db, cfg, ActionRequest(f.id, "ignore"), "ui")
    undo(db, cfg, done.id)
    with pytest.raises(UndoError):
        undo(db, cfg, done.id)


def test_undo_unknown_log_rejected(db, cfg):
    with pytest.raises(UndoError):
        undo(db, cfg, 12345)


@respx.mock
def test_undo_arr_error_logged_and_original_kept(db, cfg):
    f = add_finding(db)
    orig = log_row(db, f, "unmonitor", {"monitored": {"1": True}}, instance="r1",
                   instance_name="My Radarr")
    respx.put(f"{R}/api/v3/movie/editor").respond(500)
    row = undo(db, cfg, orig.id)
    assert row.result == "error" and "radarrkey123" not in row.message
    assert row.undo_of_id == orig.id
    db.refresh(orig)
    assert orig.undone_at is None
    assert db.get(Finding, f.id).automation_hold is False


def test_auto_undo_of_auto_action_does_not_set_hold(db, cfg):
    f = add_finding(db)
    done = execute(db, cfg, ActionRequest(f.id, "ignore"), "auto")
    row = undo(db, cfg, done.id, actor="auto")
    assert row.result == "ok" and row.actor == "auto"
    db.refresh(f)
    assert f.automation_hold is False


@respx.mock
def test_undo_partial_delete_failure_restores_monitoring(db, cfg):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance")
    add_episodes(db)
    respx.get(f"{S}/api/v3/episode").respond(json=[episode(501, True, 9001)])
    respx.get(f"{S}/api/v3/episodefile").respond(json=[episode_file(9001)])
    mon = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    respx.delete(f"{S}/api/v3/episodefile/bulk").respond(500)
    done = execute(db, cfg, ActionRequest(f.id, "delete", scope="episodes",
                                          episode_ids=[501]), "ui")
    assert done.result == "error"
    row = undo(db, cfg, done.id)
    assert row.result == "ok", row.message
    assert body(mon) == {"episodeIds": [501], "monitored": True}
    db.refresh(done)
    assert done.undone_at is not None


@respx.mock
def test_undo_updates_tracked_snapshot(db, cfg):
    from overlaparr.db.models import TrackedEpisode

    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance")
    add_episodes(db, eps=((501, False, None), (502, False, None)))
    orig = log_row(db, f, "unmonitor", {"monitored": {"501": True}},
                   scope={"scope": "episodes", "episode_ids": [501]})
    respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    assert undo(db, cfg, orig.id).result == "ok"
    db.expire_all()
    got = {e.episode_arr_id: e.monitored for e in db.scalars(select(TrackedEpisode))}
    assert got == {501: True, 502: False}
