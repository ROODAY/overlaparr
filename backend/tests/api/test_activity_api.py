from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from overlaparr.config.schema import InstanceConfig
from overlaparr.db.models import ActionLog, Finding
from tests.api.conftest import USER, wait_idle


def _finding(db, review_state="ignored"):
    f = Finding(instance_id="r1", arr_id=7, kind="movie", type="on_disk", coverage="full",
                best_quality="better", edition_summary="same", first_seen_scan_id=1,
                last_seen_scan_id=1, review_state=review_state)
    db.add(f)
    db.flush()
    return f


def _log(db, **kw):
    row = ActionLog(**{
        "actor": "ui", "instance_id": "r1", "instance_name": "Radarr", "arr_id": 7,
        "item_title": "Film", "action": "unmonitor", "scope": {"scope": "item"},
        "prior_state": {}, "result": "ok", "undoable": False, **kw})
    db.add(row)
    db.flush()
    return row


def test_activity_filters_and_pagination(authed):
    app = authed.app
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with app.state.db_factory() as db:
        for i in range(5):
            _log(db, ts=t0 + timedelta(days=i), actor="auto", rule_id="r-a", rule_name="A")
        _log(db, ts=t0 + timedelta(days=10), action="tag", instance_id="s1",
             instance_name="Sonarr")
        db.commit()

    body = authed.get("/api/v1/activity").json()
    assert body["total"] == 6
    assert body["items"][0]["action"] == "tag"  # newest first

    assert authed.get("/api/v1/activity?actor=auto").json()["total"] == 5
    assert authed.get("/api/v1/activity?action=tag").json()["total"] == 1
    assert authed.get("/api/v1/activity?instance=s1").json()["total"] == 1
    assert authed.get("/api/v1/activity?rule=r-a").json()["total"] == 5
    r = authed.get("/api/v1/activity", params={"since": "2026-01-02T00:00:00+00:00",
                                                "until": "2026-01-04T00:00:00+00:00"})
    assert r.json()["total"] == 3

    page = authed.get("/api/v1/activity?page=2&page_size=4").json()
    assert page["total"] == 6
    assert len(page["items"]) == 2
    assert authed.get("/api/v1/activity?page_size=201").status_code == 422
    assert authed.get("/api/v1/activity?page=0").status_code == 422

    item = body["items"][0]
    assert set(item) == {"id", "ts", "actor", "rule_id", "rule_name", "scan_run_id",
                         "instance_id", "instance_name", "finding_id", "arr_id", "item_title",
                         "action", "scope", "prior_state", "result", "message", "undoable",
                         "undone_at", "undo_of_id", "can_undo"}


def test_can_undo_flag(authed):
    app = authed.app
    with app.state.db_factory() as db:
        ids = {
            "ok": _log(db, undoable=True).id,
            "not_undoable": _log(db, undoable=False).id,
            "undone": _log(db, undoable=True, undone_at=datetime.now(UTC)).id,
            "partial": _log(db, undoable=True, result="error",
                            prior_state={"monitored": {"7": True}}).id,
            "failed": _log(db, undoable=True, result="error", prior_state={}).id,
        }
        db.commit()
    flags = {i["id"]: i["can_undo"] for i in authed.get("/api/v1/activity").json()["items"]}
    assert {k: flags[v] for k, v in ids.items()} == {
        "ok": True, "not_undoable": False, "undone": False, "partial": True, "failed": False,
    }


def test_undo_endpoint(authed):
    app = authed.app
    with app.state.db_factory() as db:
        f = _finding(db, review_state="ignored")
        row = _log(db, finding_id=f.id, action="ignore", undoable=True,
                   prior_state={"review_state": "reviewed"})
        db.commit()
        log_id, finding_id = row.id, f.id

    assert authed.post("/api/v1/activity/9999/undo").status_code == 404
    r = authed.post(f"/api/v1/activity/{log_id}/undo")
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["undo_of_id"] == log_id
    assert new["result"] == "ok"
    assert new["actor"] == "ui"
    assert new["can_undo"] is False
    with app.state.db_factory() as db:
        assert db.get(Finding, finding_id).review_state == "reviewed"
        assert db.get(ActionLog, log_id).undone_at is not None

    again = authed.post(f"/api/v1/activity/{log_id}/undo")
    assert again.status_code == 409
    assert again.json() == {"detail": "This action cannot be undone"}


class _FakeRadarr:
    def __init__(self):
        self.calls = []

    def set_movies_monitored(self, ids, value):
        self.calls.append((ids, value))

    def close(self):
        pass


def test_undo_uses_app_client_factory(make_app):
    fake = _FakeRadarr()
    seen = []

    def factory(inst, dry_run=False):
        seen.append((inst.id, dry_run))
        return fake

    app = make_app(client_factory=factory)
    with TestClient(app) as c:
        c.post("/api/v1/setup/user", json=USER)
        app.state.store.update(lambda cfg: cfg.instances.append(InstanceConfig(
            id="r1", name="Radarr", type="radarr", url="http://radarr:7878", api_key="k")))
        with app.state.db_factory() as db:
            log_id = _log(db, undoable=True, prior_state={"monitored": {"7": True}}).id
            db.commit()
        r = c.post(f"/api/v1/activity/{log_id}/undo")
        assert r.status_code == 200, r.text
        assert r.json()["result"] == "ok"
        assert seen == [("r1", False)]
        assert fake.calls == [([7], True)]
        wait_idle(app)
