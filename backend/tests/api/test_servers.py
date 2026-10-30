from sqlalchemy import func, select

from overlaparr.db.models import ActionLog, Finding, FindingCopy, RuleState, ScanRun, Server
from overlaparr.plex.gateway import PlexUnreachable
from tests.api.conftest import wait_idle


def _connect(c):
    assert c.put("/api/v1/plex/token", json={"token": "plex-token-123"}).status_code == 200


def _seed_findings(app):
    with app.state.db_factory() as db:
        f = Finding(instance_id="r1", arr_id=1, kind="movie", type="on_disk", coverage="full",
                    best_quality="better", edition_summary="same", first_seen_scan_id=1,
                    last_seen_scan_id=1)
        db.add(f)
        db.flush()
        db.add(FindingCopy(finding_id=f.id, server_id="id-friend", quality="better",
                           edition_cmp="same", matched_by="id"))
        db.add(RuleState(rule_id="x", finding_id=f.id, coverage_hash="h",
                         last_counted_scan_id=1))
        db.commit()


def _count(app, model):
    with app.state.db_factory() as db:
        return db.scalar(select(func.count()).select_from(model))


def _seed_run(app):
    # A past scan: changing the home server from now on purges and rebuilds.
    with app.state.db_factory() as db:
        db.add(ScanRun(status="ok", trigger="manual"))
        db.commit()


def test_refresh_requires_plex(authed):
    assert authed.post("/api/v1/servers/refresh").status_code == 409


def test_refresh_and_list(authed):
    _connect(authed)
    r = authed.post("/api/v1/servers/refresh")
    assert r.status_code == 200
    rows = authed.get("/api/v1/servers").json()
    assert {s["machine_id"] for s in rows} == {"id-home", "id-friend", "id-other"}
    friend = next(s for s in rows if s["machine_id"] == "id-friend")
    assert friend["label"] == "friend"
    assert friend["is_new"] is True
    assert friend["status"] == "offline"
    assert friend["is_home"] is False
    assert {"movie_count", "show_count", "unmatchable_count", "last_indexed_at"} <= set(friend)


def test_refresh_plex_unreachable(authed, gateway, monkeypatch):
    _connect(authed)

    def boom():
        raise PlexUnreachable("cannot list Plex servers from plex.tv")

    monkeypatch.setattr(gateway, "list_servers", boom)
    r = authed.post("/api/v1/servers/refresh")
    assert r.status_code == 502


def test_change_home_server_rebuilds(authed):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    assert authed.put("/api/v1/servers/home", json={"machine_id": "nope"}).status_code == 404
    r = authed.put("/api/v1/servers/home", json={"machine_id": "id-home"})
    assert r.status_code == 200 and r.json()["scan_id"] is None  # first choice: no scan
    assert authed.post("/api/v1/scan").status_code in (200, 202)
    wait_idle(app)
    _seed_findings(app)
    runs_before = _count(app, ScanRun)

    with app.state.db_factory() as db:
        fid = db.scalar(select(Finding.id))
        db.add(ActionLog(actor="ui", instance_id="r1", instance_name="R", finding_id=fid,
                         arr_id=1, item_title="Snap", action="unmonitor", result="ok"))
        db.commit()

    r = authed.put("/api/v1/servers/home", json={"machine_id": "id-friend"})
    assert r.status_code == 200
    assert r.json()["home_server"] == "id-friend"
    assert isinstance(r.json()["scan_id"], int)
    assert app.state.store.current.plex.home_server == "id-friend"
    assert _count(app, Finding) == 0
    with app.state.db_factory() as db:  # history kept, detached from the deleted finding
        [log_row] = db.scalars(select(ActionLog).where(ActionLog.item_title == "Snap")).all()
        assert log_row.finding_id is None
    assert _count(app, FindingCopy) == 0
    assert _count(app, RuleState) == 0
    wait_idle(app)
    assert _count(app, ScanRun) == runs_before + 1
    with app.state.db_factory() as db:
        assert db.scalar(select(ScanRun.trigger).order_by(ScanRun.id.desc())) == "manual"
    rows = {s["machine_id"]: s for s in authed.get("/api/v1/servers").json()}
    assert rows["id-friend"]["is_home"] is True
    assert rows["id-home"]["is_home"] is False
    assert authed.get("/api/v1/setup/status").json()["home_server_set"] is True


def test_server_toggle_written_to_yaml(authed, tmp_path):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    assert authed.patch("/api/v1/servers/id-zzz", json={"enabled": False}).status_code == 404

    r = authed.patch("/api/v1/servers/id-friend", json={"enabled": False, "label": "Bob"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "disabled" and body["label"] == "Bob" and body["is_new"] is False
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "id-friend" in text and "Bob" in text
    ov = app.state.store.current.servers
    assert [(o.id, o.label, o.enabled) for o in ov] == [("id-friend", "Bob", False)]
    with app.state.db_factory() as db:
        assert db.get(Server, "id-friend").is_new is False

    # Partial patch keeps the other field.
    authed.patch("/api/v1/servers/id-friend", json={"enabled": True})
    ov = app.state.store.current.servers[0]
    assert (ov.label, ov.enabled) == ("Bob", True)
    rows = {s["machine_id"]: s for s in authed.get("/api/v1/servers").json()}
    assert rows["id-friend"]["status"] == "offline"


def test_change_home_server_refused_while_scanning(authed):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    _seed_findings(app)
    lock = app.state.scan_engine._lock
    lock.acquire()
    try:
        assert app.state.scan_engine.running is True
        r = authed.put("/api/v1/servers/home", json={"machine_id": "id-friend"})
        assert r.status_code == 409
        assert r.json()["detail"] == "A scan is running; try again when it finishes"
        assert app.state.store.current.plex.home_server is None
        assert _count(app, Finding) == 1
    finally:
        lock.release()
    assert app.state.scan_engine.running is False


def test_change_home_server_start_race(authed, monkeypatch):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    _seed_findings(app)
    _seed_run(app)
    # A scan grabbed the lock first: the switch never runs, nothing changes.
    monkeypatch.setattr(app.state.scan_engine, "start", lambda trigger, before=None: None)
    r = authed.put("/api/v1/servers/home", json={"machine_id": "id-friend"})
    assert r.status_code == 409
    assert r.json()["detail"] == "A scan is running; try again when it finishes"
    assert app.state.store.current.plex.home_server is None
    assert _count(app, Finding) == 1


def test_change_home_server_holds_scan_lock(authed, monkeypatch):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    _seed_run(app)
    engine, store = app.state.scan_engine, app.state.store
    seen = []
    real_update = store.update

    def update(mutator):
        seen.append(engine.running)  # config changes while the scan lock is held
        return real_update(mutator)

    monkeypatch.setattr(store, "update", update)
    r = authed.put("/api/v1/servers/home", json={"machine_id": "id-friend"})
    assert r.status_code == 200 and seen == [True]
    assert isinstance(r.json()["scan_id"], int)
    wait_idle(app)


def test_first_home_choice_starts_no_scan(authed):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    r = authed.put("/api/v1/servers/home", json={"machine_id": "id-home"})
    assert r.status_code == 200
    assert r.json() == {"home_server": "id-home", "scan_id": None}
    assert _count(app, ScanRun) == 0
    assert app.state.store.current.plex.home_server == "id-home"
    rows = {s["machine_id"]: s for s in authed.get("/api/v1/servers").json()}
    assert rows["id-home"]["is_home"] is True
    assert rows["id-friend"]["is_home"] is False
    with app.state.db_factory() as db:
        assert db.get(Server, "id-home").is_home is True
        assert db.get(Server, "id-friend").is_home is False


def test_first_home_choice_holds_scan_lock(authed, monkeypatch):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    engine, store = app.state.scan_engine, app.state.store
    seen = []
    real_update = store.update

    def update(mutator):
        seen.append(engine.running)  # the first choice is saved under the scan lock too
        return real_update(mutator)

    monkeypatch.setattr(store, "update", update)
    r = authed.put("/api/v1/servers/home", json={"machine_id": "id-home"})
    assert r.status_code == 200 and seen == [True]
    assert r.json()["scan_id"] is None
    assert engine.running is False


def test_first_home_choice_refused_while_scanning(authed):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    lock = app.state.scan_engine._lock
    lock.acquire()
    try:
        r = authed.put("/api/v1/servers/home", json={"machine_id": "id-home"})
        assert r.status_code == 409
        assert r.json()["detail"] == "A scan is running; try again when it finishes"
        assert app.state.store.current.plex.home_server is None
    finally:
        lock.release()
    assert _count(app, ScanRun) == 0


def _home_row(c):
    return next(s for s in c.get("/api/v1/servers").json() if s["machine_id"] == "id-home")


def test_scan_reports_home_reachability(authed):
    app = authed.app
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    assert authed.put("/api/v1/servers/home", json={"machine_id": "id-home"}).status_code == 200
    wait_idle(app)
    assert authed.post("/api/v1/scan").status_code in (200, 202)
    wait_idle(app)
    home = _home_row(authed)
    assert home["status"] == "online"
    assert home["last_indexed_at"] is None


def test_home_status_ignores_disabled_override(authed):
    _connect(authed)
    authed.post("/api/v1/servers/refresh")
    assert authed.put("/api/v1/servers/home", json={"machine_id": "id-home"}).status_code == 200
    wait_idle(authed.app)
    assert authed.patch("/api/v1/servers/id-home", json={"enabled": False}).status_code == 200
    home = _home_row(authed)
    assert home["enabled"] is False
    assert home["status"] == home["reachability"]
    assert home["status"] != "disabled"
