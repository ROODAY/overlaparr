import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from overlaparr.db.models import ActionLog, Finding
from tests.api.conftest import USER, wait_idle
from tests.api.test_findings_api import GB, seed


class _FakeRadarr:
    def __init__(self):
        self.calls = []

    def get_movie(self, arr_id):
        return {"id": arr_id, "monitored": True, "hasFile": True,
                "movieFile": {"id": 11, "size": 5 * GB}}

    def set_movies_monitored(self, ids, value):
        self.calls.append(("monitored", ids, value))

    def delete_movie_file(self, file_id):
        self.calls.append(("delete_file", file_id))

    def close(self):
        pass


@pytest.fixture
def env(make_app):
    fake = _FakeRadarr()
    seen = []

    def factory(inst, dry_run=False):
        seen.append((inst.id, dry_run))
        return fake

    app = make_app(client_factory=factory)
    with TestClient(app) as c:
        c.post("/api/v1/setup/user", json=USER)
        ids = seed(app)
        yield c, ids, fake, seen
        wait_idle(app)


def _log_count(app):
    with app.state.db_factory() as db:
        return db.scalar(select(func.count()).select_from(ActionLog))


def test_delete_requires_confirm(env):
    c, ids, fake, _ = env
    before = _log_count(c.app)
    body = {"requests": [{"finding_id": ids["beta"], "action": "mark_reviewed"},
                         {"finding_id": ids["alpha"], "action": "delete"}]}
    r = c.post("/api/v1/actions", json=body)
    assert r.status_code == 400
    assert r.json() == {"detail": "Delete requires confirmation"}
    r = c.post("/api/v1/actions", json={**body, "confirm": False})
    assert r.status_code == 400
    assert _log_count(c.app) == before  # nothing executed, not even the review action
    assert fake.calls == []

    r = c.post("/api/v1/actions", json={**body, "confirm": True})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [(x["action"], x["result"]) for x in rows] == [("mark_reviewed", "ok"),
                                                         ("delete", "ok")]
    assert ("delete_file", 11) in fake.calls


def test_bulk_actions(env):
    c, ids, fake, seen = env
    r = c.post("/api/v1/actions", json={"requests": [
        {"finding_id": ids["alpha"], "action": "mark_reviewed"},
        {"finding_id": ids["beta"], "action": "ignore"},
        {"finding_id": ids["alpha"], "action": "unmonitor"},
        {"finding_id": ids["alpha"], "action": "ignore_server", "server_id": "id-friend"},
    ]})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["action"] for x in rows] == ["mark_reviewed", "ignore", "unmonitor",
                                           "ignore_server"]
    assert all(x["result"] == "ok" and x["actor"] == "ui" for x in rows)
    assert rows[2]["can_undo"] is True
    assert seen == [("r1", False)]
    assert fake.calls == [("monitored", [1], False)]
    with c.app.state.db_factory() as db:
        assert db.get(Finding, ids["alpha"]).review_state == "actioned"
        assert db.get(Finding, ids["beta"]).review_state == "ignored"

    before = _log_count(c.app)
    r = c.post("/api/v1/actions", json={"requests": [
        {"finding_id": ids["alpha"], "action": "mark_new"},
        {"finding_id": 9999, "action": "mark_new"},
    ]})
    assert r.status_code == 404
    assert _log_count(c.app) == before  # nothing executed

    r = c.post("/api/v1/actions", json={"requests": [
        {"finding_id": ids["alpha"], "action": "explode"}]})
    assert r.status_code == 422


def _preview(c, *requests):
    r = c.post("/api/v1/actions/preview", json={"requests": list(requests)})
    assert r.status_code == 200, r.text
    return r.json()


def test_action_preview_bytes(env):
    c, ids, fake, seen = env
    before = _log_count(c.app)
    alpha, show = ids["alpha"], ids["show"]

    assert _preview(c, {"finding_id": alpha, "action": "delete"},
                    {"finding_id": show, "action": "delete", "scope": "episodes",
                     "episode_ids": [501, 504]}) == {"items": 2, "episodes": 2,
                                                     "bytes": (5 + 2 + 7) * GB}
    # Whole series: relevant episodes with files are counted; every file goes.
    assert _preview(c, {"finding_id": show, "action": "delete"}) == {
        "items": 1, "episodes": 3, "bytes": 16 * GB}
    assert _preview(c, {"finding_id": show, "action": "unmonitor"}) == {
        "items": 1, "episodes": 3, "bytes": 0}
    # Overlapping requests on one finding count each file once.
    assert _preview(c, {"finding_id": show, "action": "delete"},
                    {"finding_id": show, "action": "delete", "scope": "episodes",
                     "episode_ids": [501]}) == {"items": 1, "episodes": 3, "bytes": 16 * GB}
    assert _preview(c, {"finding_id": alpha, "action": "tag"}) == {
        "items": 1, "episodes": 0, "bytes": 0}

    assert c.post("/api/v1/actions/preview", json={"requests": [
        {"finding_id": 9999, "action": "delete"}]}).status_code == 404
    assert _log_count(c.app) == before
    assert fake.calls == [] and seen == []
