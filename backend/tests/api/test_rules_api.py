from datetime import UTC, datetime, timedelta

import yaml

from overlaparr.config.schema import Rule
from overlaparr.db.models import ActionLog, Finding, FindingCopy, TrackedItem

RULES = "/api/v1/rules"


def _rule(authed, **body):
    r = authed.post(RULES, json={"name": "R", **body})
    assert r.status_code == 201, r.text
    return r.json()


def test_new_rule_defaults(authed, tmp_path):
    created = _rule(authed, name="Mine")
    expected = Rule(id=created["id"], name="Mine").model_dump()
    assert created == expected
    assert created["id"]

    listed = authed.get(RULES).json()
    assert listed == [{**expected, "last_run": None, "last_affected": 0}]
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert [r["id"] for r in on_disk["rules"]] == [created["id"]]

    # An id in the body is ignored; the server always generates one.
    other = _rule(authed, id="mine", action="tag")
    assert other["id"] not in ("mine", created["id"])
    assert other["action"] == "tag"

    assert authed.post(RULES, json={"name": "x", "bogus": 1}).status_code == 422
    assert authed.post(RULES, json={"name": "x", "min_servers": 0}).status_code == 422


def test_update_delete_and_404s(authed):
    rule = _rule(authed, action="tag", min_servers=2)
    r = authed.put(f"{RULES}/{rule['id']}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json() == Rule(id=rule["id"], name="Renamed").model_dump()  # full replacement

    assert authed.put(f"{RULES}/nope", json={"name": "x"}).status_code == 404
    assert authed.delete(f"{RULES}/nope").status_code == 404
    assert authed.post(f"{RULES}/nope/dry-run").status_code == 404

    r = authed.delete(f"{RULES}/{rule['id']}")
    assert r.status_code == 204
    assert authed.get(RULES).json() == []


def test_delete_rule_requires_confirmation(authed):
    msg = {"detail": "Delete rules require confirmation"}
    r = authed.post(RULES, json={"name": "D", "action": "delete", "enabled": True})
    assert r.status_code == 422
    assert r.json() == msg

    # Disabled delete rules are fine without confirmation, and can be dry-run.
    rule = _rule(authed, action="delete")
    r = authed.put(f"{RULES}/{rule['id']}", json={"name": "D", "action": "delete",
                                                  "enabled": True})
    assert r.status_code == 422
    assert r.json() == msg

    r = authed.post(f"{RULES}/{rule['id']}/dry-run")
    assert r.status_code == 200
    assert r.json()["dry_run_next"] is True
    assert authed.get(RULES).json()[0]["dry_run_next"] is True

    r = authed.put(f"{RULES}/{rule['id']}", json={"name": "D", "action": "delete",
                                                  "enabled": True, "delete_confirmed": True})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True


def test_reorder_persists_in_yaml(authed, tmp_path):
    ids = [_rule(authed, name=n)["id"] for n in ("a", "b", "c")]
    new_order = [ids[2], ids[0], ids[1]]
    r = authed.post(f"{RULES}/reorder", json={"ids": new_order})
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()] == new_order
    assert [x["id"] for x in authed.get(RULES).json()] == new_order
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert [x["id"] for x in on_disk["rules"]] == new_order

    for bad in ([ids[0], ids[1]], [*ids, "zzz"], [ids[0], ids[0], ids[1]],
                [ids[0], ids[1], "zzz"]):
        assert authed.post(f"{RULES}/reorder", json={"ids": bad}).status_code == 422
    assert [x["id"] for x in authed.get(RULES).json()] == new_order


def _movie(db, arr_id, title, quality="better"):
    db.add(TrackedItem(instance_id="r1", arr_id=arr_id, kind="movie", title=title,
                       seen_scan_id=1))
    f = Finding(instance_id="r1", arr_id=arr_id, kind="movie", type="wanted_elsewhere",
                coverage="full", best_quality=quality, edition_summary="same",
                first_seen_scan_id=1, last_seen_scan_id=1)
    db.add(f)
    db.flush()
    db.add(FindingCopy(finding_id=f.id, server_id="A", quality=quality, edition_cmp="same",
                       matched_by="id"))
    return f


def _show(db, arr_id, title, episodes):
    db.add(TrackedItem(instance_id="s1", arr_id=arr_id, kind="series", title=title,
                       seen_scan_id=1))
    f = Finding(instance_id="s1", arr_id=arr_id, kind="series", type="wanted_elsewhere",
                coverage="full", best_quality="same", edition_summary="same",
                first_seen_scan_id=1, last_seen_scan_id=1, relevant_episode_ids=episodes)
    db.add(f)
    db.flush()
    for e in episodes:
        db.add(FindingCopy(finding_id=f.id, server_id="A", episode_arr_id=e, quality="same",
                           edition_cmp="same", matched_by="id"))
    return f


def test_preview_counts(authed, tmp_path):
    with authed.app.state.db_factory() as db:
        good = _movie(db, 1, "Good Film").id
        _movie(db, 2, "Worse Film", quality="worse")
        show = _show(db, 3, "Show", [11, 12, 13]).id
        db.commit()
    before = (tmp_path / "config.yaml").read_text(encoding="utf-8")

    r = authed.post(f"{RULES}/preview", json={"rule": {"name": "p"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert body["items"] == [
        {"finding_id": good, "title": "Good Film", "episodes": 0},
        {"finding_id": show, "title": "Show", "episodes": 3},
    ]

    r = authed.post(f"{RULES}/preview", json={"rule": {"name": "p", "quality": ["worse"],
                                                       "instances": ["r1"]}})
    assert r.json()["count"] == 1
    assert r.json()["items"][0]["title"] == "Worse Film"

    # An unconfirmed enabled delete rule can still be previewed.
    r = authed.post(f"{RULES}/preview", json={"rule": {"name": "p", "action": "delete",
                                                       "enabled": True}})
    assert r.status_code == 200
    assert r.json()["count"] == 2

    assert authed.post(f"{RULES}/preview", json={"rule": {"name": "p", "x": 1}}).status_code \
        == 422
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == before


def test_last_run_and_last_affected(authed):
    rule = _rule(authed)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with authed.app.state.db_factory() as db:
        def log(ts, scan_run_id, rule_id=rule["id"]):
            db.add(ActionLog(ts=ts, actor="auto", rule_id=rule_id, rule_name="R",
                             scan_run_id=scan_run_id, instance_id="r1", instance_name="Radarr",
                             arr_id=1, item_title="Film", action="unmonitor", result="ok"))
        log(t0, 1)
        log(t0, 1)
        log(t0 + timedelta(days=1), 2)
        log(t0 + timedelta(days=1), 2)
        log(t0 + timedelta(days=1, seconds=5), 2)
        log(t0 + timedelta(days=9), 3, rule_id="other")
        db.commit()
    listed = authed.get(RULES).json()[0]
    assert datetime.fromisoformat(listed["last_run"]) == t0 + timedelta(days=1, seconds=5)
    assert listed["last_affected"] == 3


def test_warning_copy(authed):
    r = authed.get(f"{RULES}/warning", params={"action": "delete"})
    assert r.status_code == 200
    assert r.json() == {"text": (
        "Scheduled scans will permanently delete files from disk for every matching item. "
        "Overlaparr cannot undo this. Matches against servers that are offline are never "
        "acted on.")}
    for action in ("unmonitor", "exclude", "tag", "ignore"):
        assert authed.get(f"{RULES}/warning", params={"action": action}).json()["text"]
    assert authed.get(f"{RULES}/warning", params={"action": "nuke"}).status_code == 422


def test_rules_require_auth(client):
    client.post("/api/v1/setup/user", json={"username": "alice",
                                            "password": "hunter2hunter2"})
    client.cookies.clear()
    assert client.get(RULES).status_code == 401
