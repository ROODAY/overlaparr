import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from overlaparr.db.models import ActionLog, Finding, TrackedEpisode, TrackedItem
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
    # Whole series: every episode file goes, relevant or not.
    assert _preview(c, {"finding_id": show, "action": "delete"}) == {
        "items": 1, "episodes": 4, "bytes": 16 * GB}
    assert _preview(c, {"finding_id": show, "action": "unmonitor"}) == {
        "items": 1, "episodes": 3, "bytes": 0}
    # Overlapping requests on one finding count each file once.
    assert _preview(c, {"finding_id": show, "action": "delete"},
                    {"finding_id": show, "action": "delete", "scope": "episodes",
                     "episode_ids": [501]}) == {"items": 1, "episodes": 4, "bytes": 16 * GB}
    assert _preview(c, {"finding_id": alpha, "action": "tag"}) == {
        "items": 1, "episodes": 0, "bytes": 0}

    assert c.post("/api/v1/actions/preview", json={"requests": [
        {"finding_id": 9999, "action": "delete"}]}).status_code == 404
    assert _log_count(c.app) == before
    assert fake.calls == [] and seen == []


def test_preview_counts_shared_episode_file_once(env):
    c, ids, _, _ = env
    show = ids["show"]
    with c.app.state.db_factory() as db:
        for ep in db.scalars(select(TrackedEpisode)):
            ep.episode_file_id = 7 if ep.episode_arr_id in (501, 502) else ep.episode_arr_id
            if ep.episode_arr_id in (501, 502):
                ep.file_size = 5 * GB  # one multi-episode file, reported on both episodes
        # Between the deduplicated (9 GB) and naive (14 GB) series sizes, so sort order shows it.
        db.scalar(select(TrackedItem).where(TrackedItem.arr_id == 1)).file_size = 12 * GB
        db.commit()

    assert _preview(c, {"finding_id": show, "action": "delete", "scope": "episodes",
                        "episode_ids": [501, 502]}) == {"items": 1, "episodes": 2,
                                                        "bytes": 5 * GB}
    assert _preview(c, {"finding_id": show, "action": "delete"}) == {
        "items": 1, "episodes": 4, "bytes": (5 + 4 + 7) * GB}
    items = c.get("/api/v1/findings", params={"sort": "size", "order": "desc"}).json()["items"]
    assert [(i["title"], i["yours"]["size"]) for i in items] == [
        ("Alpha", 12 * GB), ("Show", 9 * GB), ("beta", 1 * GB)]
    items = c.get("/api/v1/findings", params={"sort": "size"}).json()["items"]
    assert [i["title"] for i in items] == ["beta", "Show", "Alpha"]


def test_preview_skips_requests_execute_would_reject(env):
    c, ids, _, _ = env
    alpha, show = ids["alpha"], ids["show"]
    with c.app.state.db_factory() as db:
        db.add(TrackedEpisode(instance_id="s1", series_arr_id=50, episode_arr_id=505, season=2,
                              episode=3, title="E505", has_file=False))
        db.add(TrackedEpisode(instance_id="s1", series_arr_id=60, episode_arr_id=601, season=1,
                              episode=1, title="Other", has_file=True, file_size=99 * GB))
        db.commit()
    empty = {"items": 0, "episodes": 0, "bytes": 0}

    def ep_req(finding_id, action, episode_ids):
        return {"finding_id": finding_id, "action": action, "scope": "episodes",
                "episode_ids": episode_ids}

    assert _preview(c, ep_req(alpha, "delete", [501])) == empty  # movie with episode scope
    assert _preview(c, ep_req(show, "delete", [601])) == empty  # another series' episode
    assert _preview(c, ep_req(show, "delete", [503, 601])) == empty  # whole request rejected
    assert _preview(c, ep_req(show, "tag", [503])) == empty
    assert _preview(c, ep_req(show, "exclude", [503])) == empty
    assert _preview(c, ep_req(show, "delete", [])) == empty
    # Episode-scope delete counts only listed episodes that have files.
    assert _preview(c, ep_req(show, "delete", [503, 505])) == {
        "items": 1, "episodes": 1, "bytes": 4 * GB}
    assert _preview(c, ep_req(show, "unmonitor", [503, 505])) == {
        "items": 1, "episodes": 2, "bytes": 0}
    assert _preview(c, ep_req(show, "delete", [503]),
                    ep_req(alpha, "unmonitor", [1])) == {"items": 1, "episodes": 1,
                                                         "bytes": 4 * GB}

    bad = {"requests": [{**ep_req(show, "delete", [503]), "delete_mode": "remove_item"}],
           "confirm": True}
    assert c.post("/api/v1/actions/preview", json=bad).status_code == 422
    assert c.post("/api/v1/actions", json=bad).status_code == 422
