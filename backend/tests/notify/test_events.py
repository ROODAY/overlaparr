import pytest
import respx
from sqlalchemy import select

from overlaparr.config.schema import AppConfig
from overlaparr.db.models import (
    ActionLog,
    Finding,
    NotificationLog,
    PendingEvent,
    ScanRun,
    Server,
    TrackedItem,
)
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.notify import channels, events
from overlaparr.notify.events import dispatch_after_scan, queue_event
from overlaparr.scan.engine import ScanContext
from overlaparr.scan.reconcile import ReconcileResult
from tests.actions.helpers import RADARR, SONARR
from tests.scan import test_engine as te

ALL = dict(new_findings=True, automation_actions=True, automation_deletes=True,
           finding_lost=True, scan_problems=True, app_errors=True)


def conn(cid="w1", enabled=True, **ev):
    return {"id": cid, "name": cid, "type": "webhook", "url": f"https://x.test/{cid}",
            "enabled": enabled, "events": ev}


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(channels, "send", lambda c, m: out.append((c.id, m)))
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    return out


def add_finding(db, arr_id, ftype="wanted_elsewhere", instance="r1", title=None):
    db.add(TrackedItem(instance_id=instance, arr_id=arr_id, kind="movie",
                       title=title or f"Movie {arr_id}", seen_scan_id=1))
    f = Finding(instance_id=instance, arr_id=arr_id, kind="movie", type=ftype, coverage="full",
                best_quality="better", edition_summary="same", first_seen_scan_id=1,
                last_seen_scan_id=1)
    db.add(f)
    db.commit()
    return f.id


def ctx_for(db, connections, new_ids=(), status=None, ok=None, dry_run=False, scan_id=2):
    cfg = AppConfig(instances=[RADARR, SONARR], notifications={
        "app_url": "https://ovl.test/", "connections": connections})
    return ScanContext(scan_id, db, cfg, status or {}, ok or {"r1": True, "s1": True},
                       ReconcileResult(new_ids=list(new_ids)), {}, None, dry_run)


def unsent(db):
    return list(db.scalars(select(PendingEvent).where(PendingEvent.sent.is_(False))))


def test_batched_one_message_per_scan(db, sent):
    ids = [add_finding(db, 1), add_finding(db, 2), add_finding(db, 3)]
    queue_event(db, "finding_lost", {"finding_id": 9, "instance_id": "r1", "title": "Gone",
                                     "type": "on_disk", "servers": ["A"], "actions_taken": []})
    db.commit()
    dispatch_after_scan(ctx_for(db, [conn("a", **ALL), conn("b", **ALL)], new_ids=ids))
    assert [c for c, _ in sent] == ["a", "b"]
    _, m = sent[0]
    assert m.title == "Overlaparr: 3 new findings, 1 lost"
    assert "Movie 1" in m.text and "Gone" in m.text and "https://ovl.test/findings" in m.text
    assert "<li>Movie 2" in m.html
    assert unsent(db) == []
    assert len(list(db.scalars(select(NotificationLog)))) == 2


def test_filters_by_type(db, sent):
    a = add_finding(db, 1, "wanted_elsewhere")
    b = add_finding(db, 2, "on_disk", title="Disk Movie")
    c = add_finding(db, 3, "on_disk", instance="s1", title="Other Inst")
    dispatch_after_scan(ctx_for(db, [
        conn("t", new_findings=True, new_findings_types=["on_disk"],
             new_findings_instances=["r1"]),
        conn("none", new_findings=True, new_findings_types=["wanted_elsewhere"],
             new_findings_instances=["s1"]),
    ], new_ids=[a, b, c]))
    assert [cid for cid, _ in sent] == ["t"]
    text = sent[0][1].text
    assert "Disk Movie" in text and "Movie 1" not in text and "Other Inst" not in text
    assert unsent(db) == []


def test_delete_actions_need_flag(db, sent):
    for action in ("unmonitor", "delete"):
        db.add(ActionLog(actor="auto", rule_name="R", scan_run_id=2, instance_id="r1",
                         instance_name="Radarr", arr_id=1, item_title=f"T-{action}",
                         action=action, result="ok"))
    db.add(ActionLog(actor="ui", scan_run_id=2, instance_id="r1", instance_name="Radarr",
                     arr_id=1, item_title="Manual", action="delete", result="ok"))
    db.commit()
    dispatch_after_scan(ctx_for(db, [
        conn("plain", automation_actions=True),
        conn("del", automation_actions=True, automation_deletes=True),
        conn("delonly", automation_deletes=True),
    ]))
    texts = {cid: m.text for cid, m in sent}
    assert "T-unmonitor" in texts["plain"] and "T-delete" not in texts["plain"]
    assert "T-unmonitor" in texts["del"] and "T-delete" in texts["del"]
    assert "T-unmonitor" not in texts["delonly"] and "T-delete" in texts["delonly"]
    assert all("Manual" not in t for t in texts.values())
    assert "(rule: R)" in texts["plain"]


def test_disabled_connection_skipped(db, sent):
    queue_event(db, "app_error", {"message": "config broke"})
    db.commit()
    dispatch_after_scan(ctx_for(db, [conn("off", enabled=False, **ALL)]))
    assert sent == [] and unsent(db) == []
    assert list(db.scalars(select(NotificationLog))) == []


def test_unsubscribed_events_marked_sent(db, sent):
    queue_event(db, "app_error", {"message": "boom"})
    db.commit()
    dispatch_after_scan(ctx_for(db, [conn("n", new_findings=True)]))
    assert sent == [] and unsent(db) == []


def test_scan_problem_only_for_newly_offline(db, sent):
    db.add(Server(machine_id="A", name="Alpha"))
    db.add(ScanRun(status="ok", trigger="manual", summary={
        "servers": {"A": "online", "B": "offline", "C": "online"}}))
    db.commit()
    dispatch_after_scan(ctx_for(
        db, [conn("p", scan_problems=True)], scan_id=2,
        status={"A": "offline", "B": "offline", "C": "stale", "D": "online"},
        ok={"r1": False, "s1": True}))
    [(_, m)] = sent
    assert "server Alpha: offline" in m.text and "server C: stale" in m.text
    assert "server B" not in m.text and "server D" not in m.text
    assert "instance My Radarr: failed" in m.text
    assert m.title == "Overlaparr: 3 scan problems"


def test_scan_problem_without_previous_run_reports_non_live(db, sent):
    dispatch_after_scan(ctx_for(db, [conn("p", scan_problems=True)],
                                status={"A": "offline", "B": "online", "C": "disabled"}))
    [(_, m)] = sent
    assert "server A: offline" in m.text and "server B" not in m.text
    assert "server C" not in m.text


def test_dry_run_sends_nothing(db, sent):
    a = add_finding(db, 1)
    dispatch_after_scan(ctx_for(db, [conn("a", **ALL)], new_ids=[a], dry_run=True,
                                status={"A": "offline"}))
    assert sent == [] and list(db.scalars(select(PendingEvent))) == []


def test_send_failure_is_logged_not_raised(db, monkeypatch):
    def boom(c, m):
        raise channels.NotifyError("down")

    monkeypatch.setattr(channels, "send", boom)
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    a = add_finding(db, 1)
    dispatch_after_scan(ctx_for(db, [conn("a", **ALL)], new_ids=[a]))
    [log] = db.scalars(select(NotificationLog))
    assert log.success is False and unsent(db) == []


def test_dispatch_never_raises(db, monkeypatch):
    def broken(ctx):
        raise RuntimeError("token=SECRET123")

    monkeypatch.setattr(events, "_queue_new_findings", broken)
    dispatch_after_scan(ctx_for(db, [conn("a", **ALL)]))


@respx.mock
def test_notify_failure_does_not_fail_scan(tmp_path, monkeypatch):
    def boom(c, m):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(channels, "send", boom)
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    env = te.Env(tmp_path)
    env.configure(notifications={"connections": [conn("a", **ALL)]})
    te.mock_radarr([te.radarr_movie()])
    eng = env.engine()
    eng.register_post_hook(dispatch_after_scan)
    run = env.run(eng)
    assert run.status == "ok" and run.summary["hook_errors"] == []
    with env.factory() as db:
        [log] = db.scalars(select(NotificationLog))
        assert log.success is False
        assert all(e.sent for e in db.scalars(select(PendingEvent)))
    env.eng.dispose()


# ----- scan failed / application errors -----------------------------------------


def test_failed_scan_sends_scan_problem(tmp_path, sent):
    from overlaparr.logging_setup import register_secret

    register_secret("SECRET123")
    env = te.Env(tmp_path)
    env.configure(notifications={"connections": [conn("a", scan_problems=True),
                                                 conn("b", new_findings=True)]})

    def exploding(inst):
        raise RuntimeError("GET http://radarr/api/v3/movie?apikey=SECRET123 failed")

    env.client_factory = exploding
    eng = env.engine()
    eng.register_post_hook(dispatch_after_scan)
    eng.register_failure_hook(events.notify_scan_failed)
    run = env.run(eng)
    assert run.status == "failed"
    [(cid, msg)] = sent
    assert cid == "a" and "scan problem" in msg.title
    assert "failed" in msg.text and "SECRET123" not in msg.text + msg.title
    assert "apikey" in msg.text  # the (redacted) error is included
    with env.factory() as db:
        assert all(e.sent for e in db.scalars(select(PendingEvent)))
    env.eng.dispose()


def test_failed_scan_notification_errors_never_escape(tmp_path, monkeypatch):
    env = te.Env(tmp_path)
    env.configure(notifications={"connections": [conn("a", scan_problems=True)]})
    env.client_factory = lambda inst: (_ for _ in ()).throw(RuntimeError("boom"))

    def broken(*a, **kw):
        raise RuntimeError("notify broke")

    monkeypatch.setattr(events, "send_pending", broken)
    eng = env.engine()
    eng.register_failure_hook(events.notify_scan_failed)
    assert env.run(eng).status == "failed"
    assert eng.running is False
    env.eng.dispose()


def _store(tmp_path, connections):
    import json

    from overlaparr.config.store import ConfigStore

    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({"notifications": {"connections": connections}}),
                    encoding="utf-8")
    store = ConfigStore(path, tmp_path / "secrets.yaml")
    store.load()
    return store, path


def test_invalid_config_sends_app_error_once_per_distinct_error(tmp_path, db, sent):
    store, path = _store(tmp_path, [conn("a", app_errors=True), conn("b", scan_problems=True)])
    factory = make_sessionmaker(db.get_bind())
    notifier = events.AppErrorNotifier(store, factory, background=False)
    store.subscribe_errors(notifier)

    path.write_text("scan: {page_size: lots}\n", encoding="utf-8")
    assert store.reload_from_disk() is False and store.error
    path.write_text("scan: {page_size: 'lots'}  # same problem\n", encoding="utf-8")
    store.reload_from_disk()
    assert [c for c, _ in sent] == ["a"]
    assert "page_size" in sent[0][1].text and "error" in sent[0][1].title
    path.write_text("scan: {stale_after_hours: -1}\n", encoding="utf-8")
    store.reload_from_disk()
    assert [c for c, _ in sent] == ["a", "a"]
    db.expire_all()
    kinds = [e.kind for e in db.scalars(select(PendingEvent))]
    assert kinds == ["app_error", "app_error"]


def test_app_error_notifier_never_raises(tmp_path, db, monkeypatch):
    store, path = _store(tmp_path, [conn("a", app_errors=True)])
    factory = make_sessionmaker(db.get_bind())

    def broken(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(events, "_deliver", broken)
    store.subscribe_errors(events.AppErrorNotifier(store, factory, background=False))
    path.write_text("scan: {page_size: lots}\n", encoding="utf-8")
    assert store.reload_from_disk() is False  # the reload itself is unaffected
    assert store.error


def test_repeated_scan_failure_notifies_once_until_a_scan_succeeds(tmp_path, sent):
    env = te.Env(tmp_path)
    env.configure(notifications={"connections": [conn("a", scan_problems=True)]})

    def failing(message):
        def factory(inst):
            raise RuntimeError(message)
        return factory

    env.client_factory = failing("Plex said no")
    eng = env.engine()
    eng.register_failure_hook(events.notify_scan_failed)
    assert env.run(eng).status == "failed"
    assert len(sent) == 1
    assert env.run(eng).status == "failed"  # the same error again: suppressed
    assert len(sent) == 1

    env.client_factory = failing("Plex said something else")
    assert env.run(eng).status == "failed"  # a distinct error is worth a message
    assert len(sent) == 2

    # Success resets: the next failure notifies again.
    from overlaparr.arr.snapshot import make_client

    te.mock_radarr([te.radarr_movie()])
    env.client_factory = make_client
    assert env.run(eng).status == "ok"
    env.client_factory = failing("Plex said something else")
    assert env.run(eng).status == "failed"
    assert len(sent) == 3
    env.eng.dispose()


def test_suppressed_failure_state_survives_a_restart(tmp_path, sent):
    env = te.Env(tmp_path)
    env.configure(notifications={"connections": [conn("a", scan_problems=True)]})

    def failing(inst):
        raise RuntimeError("same boom every time")

    env.client_factory = failing
    eng = env.engine()
    eng.register_failure_hook(events.notify_scan_failed)
    assert env.run(eng).status == "failed"
    assert len(sent) == 1
    env.eng.dispose()

    restarted = te.Env(tmp_path)  # a new process: the dedupe lives in the database
    restarted.configure(notifications={"connections": [conn("a", scan_problems=True)]})
    restarted.client_factory = failing
    eng2 = restarted.engine()
    eng2.register_failure_hook(events.notify_scan_failed)
    assert restarted.run(eng2).status == "failed"
    assert len(sent) == 1
    restarted.eng.dispose()
