import json

import pytest
import respx
from sqlalchemy import select

from overlaparr.arr.snapshot import make_client
from overlaparr.automation import runner
from overlaparr.automation.runner import detect_manual_changes, run_rules
from overlaparr.config.store import ConfigStore
from overlaparr.db.models import (
    ActionLog,
    Finding,
    FindingCopy,
    RuleState,
    TrackedEpisode,
    TrackedItem,
)
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
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
    episode,
    episode_file,
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
def make_env(db, tmp_path):
    envs = []

    def make(rules):
        envs.append(Env(db, tmp_path, rules))
        return envs[-1]

    yield make
    for env in envs:
        for c in env.opened:
            c.close()


def rule(rid="ru1", **kw) -> dict:
    base = {"id": rid, "name": f"Rule {rid}", "enabled": True, "action": "unmonitor",
            "finding_types": ["on_disk"], "wait_scans": 1}
    base.update(kw)
    return base


class Env:
    def __init__(self, db, tmp_path, rules):
        self.db = db
        path = tmp_path / "config.yaml"
        path.write_text(json.dumps({"instances": [RADARR, SONARR], "rules": rules}),
                        encoding="utf-8")
        self.store = ConfigStore(path, tmp_path / "secrets.yaml")
        self.store.load()
        self.opened = []

    def ctx(self, scan_id, status=None, ok=None, dry_run=False) -> ScanContext:
        cfg = self.store.current
        clients = {i.id: make_client(i) for i in cfg.instances}
        self.opened += clients.values()
        return ScanContext(scan_id, self.db, cfg, status or {"A": "online", "B": "online"},
                           ok or {"r1": True, "s1": True}, ReconcileResult(), clients,
                           self.store, dry_run)

    def run(self, scan_id, **kw) -> list[ActionLog]:
        return run_rules(self.ctx(scan_id, **kw))


def copy(db, f, server="A", episode=None, **kw):
    db.add(FindingCopy(finding_id=f.id, server_id=server, episode_arr_id=episode,
                       quality=kw.get("quality", "same"), edition_cmp="same", matched_by="id"))
    db.commit()


def movie_finding(db, monitored=True):
    f = add_finding(db)
    db.scalar(select(TrackedItem)).monitored = monitored
    copy(db, f)
    return f


def series_finding(db, covered=(501, 502)):
    f = add_finding(db, instance="s1", arr_id=5, kind="series", title="Severance",
                    relevant_episode_ids=[501, 502])
    add_episodes(db)
    for ep in covered:
        copy(db, f, episode=ep)
    return f


def mock_movie_unmonitor(monitored=True):
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie(monitored=monitored))
    return respx.put(f"{R}/api/v3/movie/editor").respond(json=[])


def logs(db):
    return list(db.scalars(select(ActionLog).order_by(ActionLog.id)))


def state(db):
    return db.scalar(select(RuleState))


@respx.mock
def test_waits_two_scans(db, make_env):
    env = make_env([rule(wait_scans=2)])
    movie_finding(db)
    put = mock_movie_unmonitor()
    assert env.run(1) == []
    assert state(db).consecutive_scans == 1
    assert env.run(1) == []  # same scan again: counted once only
    assert state(db).consecutive_scans == 1 and not put.called
    rows = env.run(2)
    assert [r.action for r in rows] == ["unmonitor"] and rows[0].result == "ok"
    assert rows[0].actor == "auto" and rows[0].rule_id == "ru1" and rows[0].scan_run_id == 2
    assert body(put) == {"movieIds": [1], "monitored": False}


@respx.mock
def test_offline_server_pauses_counter(db, make_env):
    env = make_env([rule(wait_scans=2)])
    movie_finding(db)
    put = mock_movie_unmonitor()
    env.run(1)
    assert env.run(2, status={"A": "offline"}) == []
    assert state(db).consecutive_scans == 1
    assert env.run(3, status={"A": "disabled"}) == []
    assert state(db).consecutive_scans == 1 and not put.called
    assert len(env.run(4)) == 1
    assert state(db).consecutive_scans == 2 and put.called


@respx.mock
def test_coverage_change_resets_counter(db, make_env):
    env = make_env([rule(wait_scans=2, coverage="partial")])
    f = series_finding(db, covered=(501,))
    env.run(1)
    first = state(db).coverage_hash
    copy(db, f, episode=502)
    assert env.run(2) == []
    s = state(db)
    assert s.consecutive_scans == 1 and s.coverage_hash != first and s.last_counted_scan_id == 2
    assert non_get_calls() == []


@respx.mock
def test_no_match_deletes_rule_state(db, make_env):
    env = make_env([rule(wait_scans=3)])
    f = movie_finding(db)
    env.run(1)
    assert state(db) is not None
    f.review_state = "ignored"
    db.commit()
    env.run(2)
    assert state(db) is None


@respx.mock
def test_series_unmonitor_is_episode_scoped(db, make_env):
    env = make_env([rule()])
    series_finding(db)
    respx.get(f"{S}/api/v3/episode").respond(json=[episode(501, True, 9001), episode(502, True)])
    ep_put = respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    series_put = respx.put(f"{S}/api/v3/series/editor").respond(json=[])
    rows = env.run(1)
    assert len(rows) == 1 and rows[0].result == "ok", rows[0].message
    assert rows[0].scope == {"scope": "episodes", "episode_ids": [501, 502]}
    assert body(ep_put) == {"episodeIds": [501, 502], "monitored": False}
    assert not series_put.called


@respx.mock
def test_series_delete_never_removes_series(db, make_env):
    env = make_env([rule(action="delete", delete_mode="remove_item",
                                  add_exclusion=True, delete_confirmed=True)])
    series_finding(db)
    respx.get(f"{S}/api/v3/episode").respond(json=[episode(501, True, 9001), episode(502, True)])
    respx.get(f"{S}/api/v3/episodefile").respond(json=[episode_file(9001)])
    respx.put(f"{S}/api/v3/episode/monitor").respond(json=[])
    bulk = respx.delete(f"{S}/api/v3/episodefile/bulk").respond(json=[])
    remove = respx.delete(f"{S}/api/v3/series/5").respond(json={})
    excl = respx.post(f"{S}/api/v3/importlistexclusion").respond(json={"id": 1})
    series_put = respx.put(f"{S}/api/v3/series/editor").respond(json=[])
    rows = env.run(1)
    assert len(rows) == 1 and rows[0].result == "ok", rows[0].message
    assert rows[0].scope == {"scope": "episodes", "episode_ids": [501, 502],
                             "delete_mode": "files", "add_exclusion": False}
    assert body(bulk) == {"episodeFileIds": [9001]}
    assert not remove.called and not excl.called and not series_put.called
    f = db.scalar(select(Finding))
    assert f.lifecycle == "active"


def test_series_request_guard_rejects_item_scope_monitoring():
    with pytest.raises(AssertionError):
        runner._guard_series(runner.ActionRequest(1, "unmonitor"))
    with pytest.raises(AssertionError):
        runner._guard_series(runner.ActionRequest(1, "delete", scope="episodes", episode_ids=[1],
                                                  delete_mode="remove_item"))
    runner._guard_series(runner.ActionRequest(1, "tag"))


@respx.mock
def test_movie_delete_uses_rule_mode(db, make_env):
    env = make_env([rule(action="delete", delete_mode="remove_item",
                                  add_exclusion=True, delete_confirmed=True)])
    movie_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    remove = respx.delete(f"{R}/api/v3/movie/1").respond(json={})
    rows = env.run(1)
    assert rows[0].result == "ok", rows[0].message
    assert rows[0].scope == {"scope": "item", "delete_mode": "remove_item", "add_exclusion": True}
    assert remove.calls.last.request.url.params["addImportExclusion"] == "true"


def remonitor_setup(db):
    f = series_finding(db)
    db.add(ActionLog(actor="auto", rule_id="ru1", rule_name="R", instance_id="s1",
                     instance_name="My Sonarr", finding_id=f.id, arr_id=5, item_title="Severance",
                     action="unmonitor", scope={"scope": "episodes", "episode_ids": [501, 502]},
                     prior_state={"monitored": {"501": True}}, result="ok", undoable=True))
    for ep in db.scalars(select(TrackedEpisode)):
        ep.monitored = False
    db.commit()
    return f


@respx.mock
def test_manual_remonitor_sets_hold(db, make_env):
    env = make_env([rule()])
    f = remonitor_setup(db)
    assert detect_manual_changes(env.ctx(2)) == []
    ep = db.scalar(select(TrackedEpisode).where(TrackedEpisode.episode_arr_id == 501))
    ep.monitored = True
    db.commit()
    assert detect_manual_changes(env.ctx(3)) == [f.id]
    db.refresh(f)
    assert f.automation_hold
    assert detect_manual_changes(env.ctx(4)) == []  # already held


@respx.mock
def test_manual_remonitor_movie_sets_hold_and_undone_row_ignored(db, make_env):
    env = make_env([rule()])
    f = movie_finding(db, monitored=True)
    row = ActionLog(actor="auto", instance_id="r1", instance_name="My Radarr", finding_id=f.id,
                    arr_id=1, item_title="The Matrix", action="unmonitor",
                    scope={"scope": "item"}, prior_state={"monitored": {"1": True}},
                    result="ok", undoable=True)
    db.add(row)
    db.commit()
    row.undone_at = row.ts
    db.commit()
    assert detect_manual_changes(env.ctx(2)) == []
    row.undone_at = None
    db.commit()
    assert detect_manual_changes(env.ctx(3)) == [f.id]


@respx.mock
def test_hold_blocks_rule(db, make_env):
    env = make_env([rule()])
    f = remonitor_setup(db)
    for ep in db.scalars(select(TrackedEpisode)):
        ep.monitored = True
    db.commit()
    assert env.run(2) == []
    db.refresh(f)
    assert f.automation_hold
    assert non_get_calls() == [] and len(logs(db)) == 1


@respx.mock
def test_already_unmonitored_skipped_no_log(db, make_env):
    env = make_env([rule()])
    movie_finding(db, monitored=False)
    assert env.run(1) == []
    assert logs(db) == [] and respx.calls.call_count == 0


@respx.mock
def test_already_unmonitored_episodes_skipped_no_log(db, make_env):
    env = make_env([rule()])
    series_finding(db)
    for ep in db.scalars(select(TrackedEpisode)):
        ep.monitored = False
    db.commit()
    assert env.run(1) == []
    assert logs(db) == [] and respx.calls.call_count == 0


@respx.mock
def test_prior_exclude_skipped_no_log(db, make_env):
    env = make_env([rule(action="exclude")])
    movie_finding(db)
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.get(f"{R}/api/v3/exclusions").respond(json=[])
    post = respx.post(f"{R}/api/v3/exclusions").respond(json={"id": 4})
    assert [r.result for r in env.run(1)] == ["ok"]
    assert env.run(2) == []
    assert post.call_count == 1 and len(logs(db)) == 1


@respx.mock
def test_tag_skipped_when_known_tag_present(db, make_env):
    env = make_env([rule(action="tag")])
    movie_finding(db)
    respx.get(f"{R}/api/v3/tag").respond(json=[{"id": 7, "label": "dupe-elsewhere"}])
    respx.get(f"{R}/api/v3/movie/1").respond(json=movie())
    respx.put(f"{R}/api/v3/movie/editor").respond(json=[])
    assert [r.prior_state for r in env.run(1)] == [{"tags_added": [7]}]
    db.scalar(select(TrackedItem)).tag_ids = [7]  # next scan's snapshot
    db.commit()
    assert env.run(2) == [] and len(logs(db)) == 1


@respx.mock
def test_dry_run_next_zero_writes_and_resets_flag(db, make_env):
    env = make_env([rule(enabled=False, dry_run_next=True), rule("ru2", enabled=False)])
    f = movie_finding(db)
    mock_movie_unmonitor()
    rows = env.run(1)
    assert [(r.rule_id, r.result) for r in rows] == [("ru1", "dry_run")]
    assert non_get_calls() == []
    assert [r.dry_run_next for r in env.store.current.rules] == [False, False]
    db.refresh(f)
    assert f.review_state == "new"
    assert env.run(2) == []  # flag consumed; rule still disabled


@respx.mock
def test_scan_dry_run_all_rules_dry_and_flags_kept(db, make_env):
    env = make_env([rule(dry_run_next=True), rule("ru2", action="exclude")])
    movie_finding(db)
    mock_movie_unmonitor()
    respx.get(f"{R}/api/v3/exclusions").respond(json=[])
    rows = env.run(1, dry_run=True)
    assert [r.result for r in rows] == ["dry_run", "dry_run"]
    assert non_get_calls() == []
    assert env.store.current.rules[0].dry_run_next is True


@respx.mock
def test_rules_run_in_order_and_later_still_eligible(db, make_env):
    env = make_env([rule("ru1", action="exclude"), rule("ru2", action="unmonitor")])
    movie_finding(db)
    respx.get(f"{R}/api/v3/exclusions").respond(json=[])
    respx.post(f"{R}/api/v3/exclusions").respond(json={"id": 4})
    mock_movie_unmonitor()
    rows = env.run(1)
    assert [(r.rule_id, r.action, r.result) for r in rows] == [
        ("ru1", "exclude", "ok"), ("ru2", "unmonitor", "ok")]
    assert [r.id for r in logs(db)] == [r.id for r in rows]


@respx.mock
def test_rule_failure_is_isolated(db, make_env, monkeypatch):
    env = make_env([rule("ru1", action="exclude"), rule("ru2")])
    movie_finding(db)
    mock_movie_unmonitor()
    real = runner.execute

    def boom(db_, cfg, req, *a, **kw):
        if req.action == "exclude":
            raise RuntimeError("kaboom apikey=sonarrkey123")
        return real(db_, cfg, req, *a, **kw)

    monkeypatch.setattr(runner, "execute", boom)
    rows = env.run(1)
    assert [r.rule_id for r in rows] == ["ru2"]


@respx.mock
def test_skips_unreachable_instance_and_disabled_rule(db, make_env):
    env = make_env([rule("ru1", enabled=False), rule("ru2")])
    movie_finding(db)
    db.add(RuleState(rule_id="ru1", finding_id=1, consecutive_scans=5, coverage_hash="x",
                     last_counted_scan_id=1))
    db.commit()
    assert env.run(2, ok={"r1": False, "s1": True}) == []
    assert respx.calls.call_count == 0
    kept = db.scalar(select(RuleState).where(RuleState.rule_id == "ru1"))
    assert kept.consecutive_scans == 5 and kept.coverage_hash == "x"


@respx.mock
def test_unconfirmed_delete_skipped_at_runtime(db, make_env, caplog):
    env = make_env([rule(enabled=False, action="delete")])
    movie_finding(db)
    ctx = env.ctx(1)
    ctx.config.rules[0].enabled = True  # bypass schema to simulate a bad in-memory rule
    assert run_rules(ctx) == []
    assert respx.calls.call_count == 0
    assert "delete_confirmed" in caplog.text
