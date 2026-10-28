import json
import time

from overlaparr.db.models import ScanRun
from tests.api.conftest import wait_idle


def test_scan_conflict_409(authed):
    app = authed.app
    lock = app.state.scan_engine._lock
    assert lock.acquire(timeout=5)
    try:
        r = authed.post("/api/v1/scan")
        assert r.status_code == 409
        assert r.json() == {"detail": "Scan already running"}
    finally:
        lock.release()

    r = authed.post("/api/v1/scan")
    assert r.status_code == 202
    scan_id = r.json()["scan_id"]
    assert isinstance(scan_id, int)
    wait_idle(app)

    status = authed.get("/api/v1/scan/status").json()
    assert status["progress"]["running"] is False
    run = status["runs"][0]
    assert run["id"] == scan_id
    assert run["trigger"] == "manual"
    assert run["status"] == "failed"  # Plex is not configured
    assert set(run) == {"id", "started_at", "finished_at", "status", "trigger", "error",
                        "summary"}


def test_scan_status_last_ten_newest_first(authed):
    app = authed.app
    with app.state.db_factory() as db:
        for _ in range(12):
            db.add(ScanRun(status="ok", trigger="schedule", summary={}))
        db.commit()
    runs = authed.get("/api/v1/scan/status").json()["runs"]
    assert [r["id"] for r in runs] == list(range(12, 2, -1))


def _events(lines):
    for line in lines:
        if line.startswith("data: "):
            yield json.loads(line[len("data: "):])


def test_sse_streams_events(authed):
    bus = authed.app.state.bus
    bus.publish({"phase": "servers", "running": True})
    with authed.stream("GET", "/api/v1/scan/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _events(r.iter_lines())
        first = next(events)
        assert first["phase"] == "servers"
        bus.publish({"phase": "indexing", "detail": "friend", "done": 1, "total": 2})
        second = next(events)
        assert second["phase"] == "indexing"
        assert second["detail"] == "friend"
        assert second["running"] is True
    # The stream's queue is released once the client goes away.
    for _ in range(50):
        if not bus._subscribers:
            break
        time.sleep(0.05)
    assert bus._subscribers == []


def test_startup_marks_interrupted_scans_failed(make_app):
    from overlaparr.db.models import ScanRun

    app = make_app()
    with app.state.db_factory() as db:
        db.add(ScanRun(status="running", trigger="schedule", summary={}))
        db.add(ScanRun(status="ok", trigger="manual", summary={}))
        db.commit()
    app.state.db_engine.dispose()
    app = make_app()  # restart
    with app.state.db_factory() as db:
        runs = {r.trigger: r for r in db.query(ScanRun)}
    assert runs["schedule"].status == "failed" and runs["schedule"].error == "interrupted"
    assert runs["schedule"].finished_at is not None
    assert runs["manual"].status == "ok" and runs["manual"].error is None
    app.state.db_engine.dispose()


def test_app_wires_scan_failed_and_app_error_notifications(make_app, tmp_path, monkeypatch):
    from overlaparr.notify import channels

    sent = []
    monkeypatch.setattr(channels, "send", lambda c, m: sent.append((c.id, m.title)))
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    conn = {"id": "w1", "name": "w1", "type": "webhook", "url": "https://x.test/w1",
            "enabled": True, "events": {"scan_problems": True, "app_errors": True}}
    (tmp_path / "config.yaml").write_text(json.dumps({"notifications": {"connections": [conn]}}),
                                          encoding="utf-8")
    app = make_app()
    app.state.scan_engine.run_sync("manual")  # Plex not configured -> the scan fails
    assert sent == [("w1", "Overlaparr: 1 scan problem")]
    (tmp_path / "config.yaml").write_text("scan: {page_size: lots}\n", encoding="utf-8")
    app.state.store.reload_from_disk()
    deadline = time.monotonic() + 10
    while len(sent) < 2 and time.monotonic() < deadline:  # delivered on a daemon thread
        time.sleep(0.05)
    assert sent[1] == ("w1", "Overlaparr: 1 error")
    app.state.db_engine.dispose()


def test_failure_dedupe_marker_is_not_exposed(authed):
    from overlaparr.notify.events import FAILURE_NOTIFIED

    with authed.app.state.db_factory() as db:
        db.add(ScanRun(status="failed", trigger="schedule", error="boom",
                       summary={FAILURE_NOTIFIED: "boom", "counts": {"lost": 0}}))
        db.commit()
    run = authed.get("/api/v1/scan/status").json()["runs"][0]
    assert run["error"] == "boom" and run["summary"] == {"counts": {"lost": 0}}
