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
