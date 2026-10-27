from datetime import UTC, datetime

from overlaparr.config.schema import InstanceConfig, ServerOverride
from overlaparr.db.models import Finding, ScanRun, Server, TrackedItem


def _finding(db, arr_id, **kw):
    db.add(Finding(**{"instance_id": "r1", "arr_id": arr_id, "kind": "movie", "type": "on_disk",
                      "coverage": "full", "best_quality": "better", "edition_summary": "same",
                      "first_seen_scan_id": 1, "last_seen_scan_id": 1, **kw}))


def _tracked(db, arr_id, size):
    db.add(TrackedItem(instance_id="r1", arr_id=arr_id, kind="movie", title=f"M{arr_id}",
                       has_file=True, file_size=size, seen_scan_id=1))


def _configure(c):
    c.plex.home_server = "id-home"
    c.servers.append(ServerOverride(id="id-friend", label="Bob's"))
    c.instances.append(InstanceConfig(id="r1", name="Radarr", type="radarr",
                                      url="http://radarr:7878", api_key="k"))
    c.instances.append(InstanceConfig(id="s1", name="Sonarr", type="sonarr",
                                      url="http://sonarr:8989", api_key="k"))


def test_dashboard_reclaimable(authed):
    app = authed.app
    indexed = datetime(2026, 10, 1, tzinfo=UTC)
    app.state.store.update(_configure)
    with app.state.db_factory() as db:
        _finding(db, 1)
        _tracked(db, 1, 1000)
        _finding(db, 2, review_state="reviewed", coverage="partial")
        _tracked(db, 2, 500)
        _finding(db, 3, review_state="ignored")  # ignored: not reclaimable
        _tracked(db, 3, 7000)
        _finding(db, 4, type="wanted_elsewhere", review_state="actioned")
        _finding(db, 5, lifecycle="lost")
        _tracked(db, 5, 9000)
        _finding(db, 6, lifecycle="resolved")
        db.add(Server(machine_id="id-home", name="home", reachability="online", is_home=True))
        db.add(Server(machine_id="id-friend", name="friend", reachability="online",
                      last_indexed_at=indexed))
        db.add(Server(machine_id="id-other", name="other", reachability="offline"))
        db.add(ScanRun(status="ok", trigger="schedule", summary={"instances": {"r1": False}}))
        db.add(ScanRun(status="ok", trigger="manual", summary={"instances": {"r1": True}},
                       finished_at=indexed))
        db.commit()

    r = authed.get("/api/v1/dashboard")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["counts"] == {
        "by_type": {"wanted_elsewhere": 1, "on_disk": 3},
        "by_coverage": {"full": 3, "partial": 1},
        "by_state": {"new": 1, "reviewed": 1, "ignored": 1, "actioned": 1},
    }
    assert d["reclaimable_bytes"] == 1500
    assert d["lost"] == 1
    assert d["last_scan"]["id"] == 2
    assert d["last_scan"]["trigger"] == "manual"
    assert datetime.fromisoformat(d["next_scan"]) > datetime.now(UTC)
    servers = {s["id"]: s for s in d["servers"]}
    assert set(servers) == {"id-friend", "id-other"}
    assert servers["id-friend"]["label"] == "Bob's"
    assert servers["id-friend"]["reachability"] == "online"
    assert datetime.fromisoformat(servers["id-friend"]["last_indexed_at"]) == indexed
    assert servers["id-other"]["label"] == "other"
    assert servers["id-other"]["last_indexed_at"] is None
    assert d["instances"] == [{"id": "r1", "name": "Radarr", "ok": True},
                              {"id": "s1", "name": "Sonarr", "ok": None}]
    assert d["config_error"] is None


def test_dashboard_next_scan_from_scheduler_job(authed):
    app = authed.app
    sched = app.state.scheduler
    sched.apply(app.state.store.current)  # jobs exist; background scheduler not started
    expected = datetime(2030, 1, 1, tzinfo=UTC)
    sched.scheduler.get_job("scan").next_run_time = expected
    assert datetime.fromisoformat(authed.get("/api/v1/dashboard").json()["next_scan"]) == expected


def test_dashboard_empty(authed):
    d = authed.get("/api/v1/dashboard").json()
    assert d["counts"]["by_type"] == {"wanted_elsewhere": 0, "on_disk": 0}
    assert d["counts"]["by_state"] == {"new": 0, "reviewed": 0, "ignored": 0, "actioned": 0}
    assert d["reclaimable_bytes"] == 0
    assert d["lost"] == 0
    assert d["last_scan"] is None
    assert d["servers"] == []
    assert d["instances"] == []


def test_config_error_redacted_everywhere(authed, monkeypatch):
    from overlaparr.logging_setup import register_secret

    secret = "registered-secret-987"
    register_secret(secret)
    store = authed.app.state.store
    monkeypatch.setattr(store, "_error", f"bad value {secret}; api_key: leaked-key-555")

    dash = authed.get("/api/v1/dashboard").json()["config_error"]
    settings = authed.get("/api/v1/settings").json()["config_error"]
    conflict = authed.put("/api/v1/settings", json={"tags": {"enabled": True}})
    assert conflict.status_code == 409
    for text in (dash, settings, conflict.json()["detail"]):
        assert "bad value" in text
        assert secret not in text and "leaked-key-555" not in text


def test_instance_ok_from_latest_run_with_instances(authed):
    app = authed.app
    app.state.store.update(lambda c: c.instances.append(InstanceConfig(
        id="r1", name="Radarr", type="radarr", url="http://radarr:7878", api_key="k")))
    with app.state.db_factory() as db:
        db.add(ScanRun(status="ok", trigger="schedule", summary={"instances": {"r1": False}}))
        db.add(ScanRun(status="failed", trigger="manual", summary={}))  # failed before instances
        db.commit()
    d = authed.get("/api/v1/dashboard").json()
    assert d["last_scan"]["status"] == "failed"
    assert d["instances"] == [{"id": "r1", "name": "Radarr", "ok": False}]


def test_reclaimable_counts_shared_episode_file_once(authed):
    from overlaparr.db.models import TrackedEpisode

    app = authed.app
    with app.state.db_factory() as db:
        _finding(db, 50, instance_id="s1", kind="series", relevant_episode_ids=[1, 2, 3])
        for ep_id, file_id, size in ((1, 7, 500), (2, 7, 500), (3, None, 200)):
            # Episodes 1 and 2 are one multi-episode file, reported on both.
            db.add(TrackedEpisode(instance_id="s1", series_arr_id=50, episode_arr_id=ep_id,
                                  season=1, episode=ep_id, title=f"E{ep_id}", has_file=True,
                                  episode_file_id=file_id, file_size=size))
        db.commit()
    assert authed.get("/api/v1/dashboard").json()["reclaimable_bytes"] == 700
