import json

import pytest
import respx
from sqlalchemy import select

from overlaparr.arr.snapshot import make_client
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import ActionLog, Finding, FindingCopy, PendingEvent, TrackedItem
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.loss import handle_losses
from overlaparr.scan.engine import ScanContext
from overlaparr.scan.reconcile import ReconcileResult
from tests.actions.helpers import (
    R,
    RADARR,
    S,
    SONARR,
    add_episodes,
    add_finding,
    body,
    non_get_calls,
)
from tests.scan import test_engine as te


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


class Env:
    def __init__(self, db, tmp_path, loss=None, instances=None, ok=None, dry_run=False):
        self.db = db
        insts = instances or [dict(RADARR, loss_handling=loss or {}),
                              dict(SONARR, loss_handling=loss or {})]
        path = tmp_path / "config.yaml"
        path.write_text(json.dumps({"instances": insts}), encoding="utf-8")
        self.store = ConfigStore(path, tmp_path / "secrets.yaml")
        self.store.load()
        self.ok = ok or {"r1": True, "s1": True}
        self.dry_run = dry_run
        self.clients = []

    def run(self, lost_ids):
        cfg = self.store.current
        clients = {i.id: make_client(i) for i in cfg.instances}
        self.clients += clients.values()
        ctx = ScanContext(7, self.db, cfg, {"A": "online"}, self.ok,
                          ReconcileResult(lost_ids=list(lost_ids)), clients, self.store,
                          self.dry_run)
        handle_losses(ctx)

    def close(self):
        for c in self.clients:
            c.close()


@pytest.fixture
def make_env(db, tmp_path):
    envs = []

    def make(**kw):
        envs.append(Env(db, tmp_path, **kw))
        return envs[-1]

    yield make
    for e in envs:
        e.close()


def lost_movie(db, exclusion=False):
    f = add_finding(db)
    f.lifecycle, f.review_state = "lost", "actioned"
    db.add(FindingCopy(finding_id=f.id, server_id="A", quality="same", edition_cmp="same",
                       matched_by="id"))
    db.add(ActionLog(actor="auto", instance_id="r1", instance_name="My Radarr",
                     finding_id=f.id, arr_id=1, item_title="The Matrix", action="unmonitor",
                     scope={"scope": "item"}, prior_state={"monitored": {"1": True}},
                     result="ok", undoable=True))
    if exclusion:
        db.add(ActionLog(actor="ui", instance_id="r1", instance_name="My Radarr",
                         finding_id=f.id, arr_id=1, item_title="The Matrix", action="exclude",
                         scope={}, prior_state={"exclusion_id": 55}, result="ok", undoable=True))
    db.commit()
    return f


def events(db):
    return list(db.scalars(select(PendingEvent).order_by(PendingEvent.id)))


def logs(db):
    return list(db.scalars(select(ActionLog).order_by(ActionLog.id)))


@respx.mock
def test_default_only_notifies(db, make_env):
    f = lost_movie(db)
    make_env().run([f.id])
    assert non_get_calls() == []
    [ev] = events(db)
    assert ev.kind == "finding_lost" and ev.sent is False
    assert ev.payload == {"finding_id": f.id, "instance_id": "r1", "title": "The Matrix",
                          "type": "on_disk", "servers": ["A"], "actions_taken": []}
    assert len(logs(db)) == 1


@respx.mock
def test_remonitor_and_search(db, make_env):
    f = lost_movie(db, exclusion=True)
    put = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    post = respx.post(f"{R}/api/v3/command").respond(json={})
    make_env(loss={"remonitor": True, "search": True}).run([f.id])
    assert body(put) == {"movieIds": [1], "monitored": True}
    assert body(post) == {"name": "MoviesSearch", "movieIds": [1]}
    unmonitor, exclude, undo_row, search = logs(db)
    assert unmonitor.undone_at is not None and exclude.undone_at is None
    assert (undo_row.action, undo_row.actor, undo_row.result) == ("unmonitor", "auto", "ok")
    assert (search.action, search.actor, search.result) == ("search", "auto", "ok")
    assert search.rule_name == "loss handling" and search.scan_run_id == 7
    assert search.undoable is False
    assert db.get(Finding, f.id).automation_hold is False
    assert events(db)[0].payload["actions_taken"] == ["remonitor", "search"]


@respx.mock
def test_remove_exclusion(db, make_env):
    f = lost_movie(db, exclusion=True)
    delete = respx.delete(f"{R}/api/v3/exclusions/55").respond(status_code=200)
    make_env(loss={"remove_exclusion": True}).run([f.id])
    assert delete.called and len(non_get_calls()) == 1
    assert events(db)[0].payload["actions_taken"] == ["remove_exclusion"]


@respx.mock
def test_profile_change(db, make_env):
    f = lost_movie(db)
    db.scalar(select(TrackedItem)).quality_profile_id = 4
    db.commit()
    put = respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    make_env(loss={"quality_profile_id": 9}).run([f.id])
    assert body(put) == {"movieIds": [1], "qualityProfileId": 9}
    row = logs(db)[-1]
    assert (row.action, row.actor, row.rule_name, row.undoable) == (
        "quality_profile", "auto", "loss handling", False)
    assert row.prior_state == {"quality_profile_id": 4} and row.scan_run_id == 7
    assert events(db)[0].payload["actions_taken"] == ["quality_profile"]


@respx.mock
def test_series_search_uses_remonitored_episodes(db, make_env):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance",
                    relevant_episode_ids=[501, 502])
    f.lifecycle = "lost"
    add_episodes(db)
    db.add(ActionLog(actor="auto", instance_id="s1", instance_name="My Sonarr",
                     finding_id=f.id, arr_id=5, item_title="Severance", action="unmonitor",
                     scope={"scope": "episodes", "episode_ids": [501]},
                     prior_state={"monitored": {"501": True}}, result="ok", undoable=True))
    db.commit()
    put = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    post = respx.post(f"{S}/api/v3/command").respond(json={})
    make_env(loss={"remonitor": True, "search": True}).run([f.id])
    assert body(put) == {"episodeIds": [501], "monitored": True}
    assert body(post) == {"name": "EpisodeSearch", "episodeIds": [501]}


@respx.mock
def test_series_search_falls_back_to_relevant(db, make_env):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance",
                    relevant_episode_ids=[501, 502])
    f.lifecycle = "lost"
    db.commit()
    post = respx.post(f"{S}/api/v3/command").respond(json={})
    make_env(loss={"search": True}).run([f.id])
    assert body(post) == {"name": "EpisodeSearch", "episodeIds": [501, 502]}


@respx.mock
def test_only_lost_findings_are_touched(db, make_env):
    lost_movie(db)
    make_env(loss={"remonitor": True, "search": True}).run([])
    assert non_get_calls() == [] and events(db) == [] and len(logs(db)) == 1


@respx.mock
def test_instance_not_ok_or_removed_skips_writes_but_notifies(db, make_env):
    f = lost_movie(db)
    make_env(loss={"remonitor": True}, ok={"r1": False, "s1": True}).run([f.id])
    assert non_get_calls() == []
    assert events(db)[0].payload["actions_taken"] == []
    make_env(instances=[dict(SONARR)], ok={"s1": True}).run([f.id])
    assert non_get_calls() == [] and len(events(db)) == 2


@respx.mock
def test_dry_run_does_nothing_and_queues_nothing(db, make_env):
    f = lost_movie(db)
    make_env(loss={"remonitor": True, "search": True}, dry_run=True).run([f.id])
    assert non_get_calls() == [] and events(db) == [] and len(logs(db)) == 1


@respx.mock
def test_arr_error_is_logged_and_other_findings_continue(db, make_env):
    f1 = lost_movie(db)
    f2 = add_finding(db, arr_id=2, title="Other")
    f2.lifecycle = "lost"
    db.commit()
    respx.put(f"{R}/api/v3/movie/editor").respond(status_code=500, text="boom radarrkey123")
    post = respx.post(f"{R}/api/v3/command").respond(json={})
    make_env(loss={"remonitor": True, "search": True}).run([f1.id, f2.id])
    errs = [r for r in logs(db) if r.result == "error"]
    assert errs and all("radarrkey123" not in (r.message or "") for r in errs)
    assert post.called
    assert [e.payload["finding_id"] for e in events(db)] == [f1.id, f2.id]
    assert "remonitor" not in events(db)[0].payload["actions_taken"]


@respx.mock
def test_not_triggered_by_offline_server(tmp_path):
    env = te.Env(tmp_path)
    loss = {"remonitor": True, "remove_exclusion": True, "quality_profile_id": 9, "search": True}
    env.configure(instances=[dict(te.RADARR, loss_handling=loss)])
    te.mock_radarr([te.radarr_movie()])
    eng = env.engine()
    eng.register_post_hook(handle_losses)
    env.run(eng)
    [f] = env.findings()
    with env.factory() as db:
        db.add(ActionLog(actor="auto", instance_id="r1", instance_name="Radarr",
                         finding_id=f.id, arr_id=1, item_title="The Matrix", action="unmonitor",
                         scope={"scope": "item"}, prior_state={"monitored": {"1": True}},
                         result="ok", undoable=True))
        db.commit()
    env.gateway.servers["other"]["unreachable"] = True
    run = env.run(eng)
    assert run.status == "ok" and run.summary["counts"]["lost"] == 0
    assert non_get_calls() == []
    with env.factory() as db:
        assert list(db.scalars(select(PendingEvent))) == []
    assert env.findings()[0].lifecycle == "active"
    env.eng.dispose()


@respx.mock
def test_series_delete_row_remonitored_episodes_are_searched(db, make_env):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance",
                    relevant_episode_ids=[501, 502])
    f.lifecycle = "lost"
    add_episodes(db)
    db.add(ActionLog(actor="auto", instance_id="s1", instance_name="My Sonarr",
                     finding_id=f.id, arr_id=5, item_title="Severance", action="delete",
                     scope={"scope": "item"},
                     prior_state={"monitored": {"501": True, "503": True}}, result="ok",
                     undoable=True))
    db.commit()
    respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    post = respx.post(f"{S}/api/v3/command").respond(json={})
    make_env(loss={"remonitor": True, "search": True}).run([f.id])
    assert body(post) == {"name": "EpisodeSearch", "episodeIds": [501, 503]}


@respx.mock
def test_search_skipped_when_nothing_to_search(db, make_env):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance")
    f.lifecycle = "lost"
    db.commit()
    make_env(loss={"search": True}).run([f.id])
    assert non_get_calls() == [] and len(logs(db)) == 0
    assert events(db)[0].payload["actions_taken"] == []
