from datetime import UTC, datetime, timedelta

from overlaparr.config.schema import InstanceConfig, ServerOverride
from overlaparr.db.models import (
    ActionLog,
    Finding,
    FindingCopy,
    FindingServerIgnore,
    Server,
    TrackedEpisode,
    TrackedItem,
)

GB = 1024**3


def _configure(c):
    c.plex.home_server = "id-home"
    c.servers.append(ServerOverride(id="id-friend", label="Bob's"))
    c.instances.append(InstanceConfig(id="r1", name="Radarr", type="radarr",
                                      url="http://radarr:7878/", api_key="k"))
    c.instances.append(InstanceConfig(id="s1", name="Sonarr", type="sonarr",
                                      url="http://sonarr:8989", api_key="k"))


def _finding(db, instance_id, arr_id, kind, **kw):
    f = Finding(**{"instance_id": instance_id, "arr_id": arr_id, "kind": kind,
                   "type": "on_disk", "coverage": "full", "best_quality": "better",
                   "edition_summary": "same", "first_seen_scan_id": 1, "last_seen_scan_id": 1,
                   "relevant_count": 1, "covered_count": 1, **kw})
    db.add(f)
    db.flush()
    return f


def _item(db, instance_id, arr_id, kind, title, **kw):
    db.add(TrackedItem(**{"instance_id": instance_id, "arr_id": arr_id, "kind": kind,
                          "title": title, "seen_scan_id": 1, **kw}))


def _copy(db, finding, server_id, **kw):
    db.add(FindingCopy(**{"finding_id": finding.id, "server_id": server_id, "quality": "same",
                          "edition_cmp": "same", "matched_by": "id", **kw}))


def _episode(db, ep_id, season, episode, **kw):
    db.add(TrackedEpisode(**{"instance_id": "s1", "series_arr_id": 50, "episode_arr_id": ep_id,
                             "season": season, "episode": episode, "title": f"E{ep_id}", **kw}))


def seed(app):
    """Movies M1..M3 on r1, series Show on s1, plus one finding of an unconfigured instance."""
    app.state.store.update(_configure)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    ids = {}
    with app.state.db_factory() as db:
        db.add(Server(machine_id="id-home", name="home", is_home=True))
        db.add(Server(machine_id="id-friend", name="friend"))
        db.add(Server(machine_id="id-other", name="other"))

        _item(db, "r1", 1, "movie", "Alpha", year=2001, file_size=5 * GB, resolution=4,
              title_slug="alpha-2001", poster_url="http://img/alpha.jpg", has_file=True)
        f = _finding(db, "r1", 1, "movie", updated_at=t0)
        _copy(db, f, "id-friend", resolution=5, hdr=True, codec="hevc", size=40 * GB,
              quality="better", edition="Director's Cut", edition_cmp="different")
        _copy(db, f, "id-other", resolution=4, size=8 * GB, stale=True)
        db.add(FindingServerIgnore(finding_id=f.id, server_id="id-other"))
        ids["alpha"] = f.id

        _item(db, "r1", 2, "movie", "beta", year=1999, file_size=1 * GB, resolution=3)
        f = _finding(db, "r1", 2, "movie", type="wanted_elsewhere", coverage="partial",
                     best_quality="worse", edition_summary="unknown", review_state="reviewed",
                     covered_count=0, updated_at=t0 + timedelta(days=2))
        ids["beta"] = f.id

        _item(db, "r1", 3, "movie", "Gamma", year=2010, file_size=3 * GB)
        f = _finding(db, "r1", 3, "movie", lifecycle="lost", updated_at=t0)
        ids["gamma"] = f.id

        _item(db, "s1", 50, "series", "Show", year=2005, title_slug="show")
        _episode(db, 501, 1, 1, has_file=True, file_size=2 * GB, resolution=4)
        _episode(db, 502, 1, 2, has_file=True, file_size=3 * GB, resolution=3)
        _episode(db, 503, 2, 1, has_file=True, file_size=4 * GB, resolution=4)
        _episode(db, 504, 2, 2, has_file=True, file_size=7 * GB)  # not relevant
        f = _finding(db, "s1", 50, "series", relevant_episode_ids=[501, 502, 503],
                     relevant_count=3, covered_count=2, coverage="partial",
                     has_number_matches=True, updated_at=t0 + timedelta(days=1))
        _copy(db, f, "id-friend", episode_arr_id=501, resolution=4, matched_by="number")
        _copy(db, f, "id-friend", episode_arr_id=502, resolution=5, quality="better")
        ids["show"] = f.id

        _item(db, "gone", 9, "movie", "Orphan")
        _finding(db, "gone", 9, "movie")
        db.add(ActionLog(actor="ui", instance_id="r1", instance_name="Radarr",
                         finding_id=ids["alpha"], arr_id=1, item_title="Alpha", action="tag",
                         result="ok", ts=t0))
        db.add(ActionLog(actor="auto", instance_id="r1", instance_name="Radarr",
                         finding_id=ids["alpha"], arr_id=1, item_title="Alpha",
                         action="unmonitor", result="ok", ts=t0 + timedelta(days=1)))
        db.commit()
        ids["orphan_count"] = 1
    return ids


def _titles(client, **params):
    r = client.get("/api/v1/findings", params=params)
    assert r.status_code == 200, r.text
    return [i["title"] for i in r.json()["items"]]


def test_findings_filters_and_pagination(authed):
    ids = seed(authed.app)
    body = authed.get("/api/v1/findings").json()
    assert body["total"] == 3  # lost and unconfigured-instance findings excluded
    assert [i["title"] for i in body["items"]] == ["Alpha", "beta", "Show"]

    alpha = body["items"][0]
    assert alpha == {
        "id": ids["alpha"], "instance": {"id": "r1", "name": "Radarr", "type": "radarr"},
        "arr_id": 1, "kind": "movie", "type": "on_disk", "title": "Alpha", "year": 2001,
        "poster_url": "http://img/alpha.jpg", "yours": {"resolution": "1080p", "size": 5 * GB},
        "elsewhere": [
            {"server_id": "id-friend", "label": "Bob's", "resolution": "2160p", "hdr": True,
             "stale": False, "ignored": False},
            {"server_id": "id-other", "label": "other", "resolution": "1080p", "hdr": False,
             "stale": True, "ignored": True},
        ],
        "badges": {"coverage": "full", "covered_count": 1, "relevant_count": 1,
                   "best_quality": "better", "edition": "same", "has_number_matches": False},
        "review_state": "new", "lifecycle": "active", "automation_hold": False,
        "arr_url": "http://radarr:7878/movie/alpha-2001",
    }
    show = body["items"][2]
    assert show["yours"] == {"resolution": "1080p", "size": 9 * GB}
    assert show["arr_url"] == "http://sonarr:8989/series/show"
    assert [e["server_id"] for e in show["elsewhere"]] == ["id-friend"]
    assert show["elsewhere"][0]["resolution"] == "2160p"
    assert body["items"][1]["yours"] == {"resolution": "720p", "size": 1 * GB}
    assert body["items"][1]["arr_url"] is None  # no slug

    assert _titles(authed, type="wanted_elsewhere") == ["beta"]
    assert _titles(authed, type="wanted_elsewhere,on_disk") == ["Alpha", "beta", "Show"]
    assert _titles(authed, coverage="partial") == ["beta", "Show"]
    assert _titles(authed, quality="worse") == ["beta"]
    assert _titles(authed, edition="unknown") == ["beta"]
    assert _titles(authed, match_method="number") == ["Show"]
    assert _titles(authed, match_method="id") == ["Alpha", "beta"]
    assert _titles(authed, instance="s1") == ["Show"]
    assert _titles(authed, server="id-other") == ["Alpha"]
    assert _titles(authed, server="id-friend,id-other") == ["Alpha", "Show"]
    assert _titles(authed, review_state="reviewed") == ["beta"]
    assert _titles(authed, lifecycle="lost") == ["Gamma"]
    assert _titles(authed, lifecycle="all") == ["Alpha", "beta", "Gamma", "Show"]
    assert _titles(authed, q="ALP") == ["Alpha"]

    assert _titles(authed, sort="title", order="desc") == ["Show", "beta", "Alpha"]
    assert _titles(authed, sort="year") == ["beta", "Alpha", "Show"]
    assert _titles(authed, sort="size", order="desc") == ["Show", "Alpha", "beta"]
    assert _titles(authed, sort="updated", order="desc") == ["beta", "Show", "Alpha"]
    assert _titles(authed, sort="servers", order="desc") == ["Alpha", "Show", "beta"]

    page = authed.get("/api/v1/findings", params={"page": 2, "page_size": 2}).json()
    assert page["total"] == 3
    assert [i["title"] for i in page["items"]] == ["Show"]
    assert authed.get("/api/v1/findings?page_size=201").status_code == 422
    assert authed.get("/api/v1/findings?page=0").status_code == 422
    assert authed.get("/api/v1/findings?sort=bogus").status_code == 422


def test_finding_detail_movie_copies_and_history(authed):
    ids = seed(authed.app)
    d = authed.get(f"/api/v1/findings/{ids['alpha']}").json()
    assert d["title"] == "Alpha"
    assert d["copies"] == [
        {"server_id": "id-friend", "label": "Bob's", "resolution": "2160p", "hdr": True,
         "codec": "hevc", "edition": "Director's Cut", "size": 40 * GB, "quality": "better",
         "edition_cmp": "different", "stale": False, "ignored": False},
        {"server_id": "id-other", "label": "other", "resolution": "1080p", "hdr": False,
         "codec": None, "edition": None, "size": 8 * GB, "quality": "same",
         "edition_cmp": "same", "stale": True, "ignored": True},
    ]
    assert [h["action"] for h in d["history"]] == ["unmonitor", "tag"]
    assert "can_undo" in d["history"][0]
    assert d["automation_hold"] is False
    assert "seasons" not in d

    assert authed.get("/api/v1/findings/9999").status_code == 404
    with authed.app.state.db_factory() as db:
        orphan = db.query(Finding).filter_by(instance_id="gone").one().id
    assert authed.get(f"/api/v1/findings/{orphan}").status_code == 404


def test_finding_detail_episode_grid_shape(authed):
    ids = seed(authed.app)
    d = authed.get(f"/api/v1/findings/{ids['show']}").json()
    assert "copies" not in d
    assert d["seasons"] == [
        {"season": 1, "relevant": 2, "covered": 2, "servers": ["Bob's"]},
        {"season": 2, "relevant": 1, "covered": 0, "servers": []},  # only servers with copies
    ]
    assert [e["episode_arr_id"] for e in d["episodes"]] == [501, 502, 503]  # relevant only
    e1 = d["episodes"][0]
    assert e1 == {
        "episode_arr_id": 501, "season": 1, "episode": 1, "title": "E501",
        "yours": {"state": "have", "resolution": "1080p", "size": 2 * GB},
        "copies": {"id-friend": {"resolution": "1080p", "hdr": False, "matched_by": "number",
                                 "stale": False, "quality": "same"}},
    }
    assert d["episodes"][2]["copies"] == {}


def test_finding_hold(authed):
    ids = seed(authed.app)
    url = f"/api/v1/findings/{ids['alpha']}/hold"
    r = authed.post(url, json={"hold": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"automation_hold": True}
    assert authed.get(f"/api/v1/findings/{ids['alpha']}").json()["automation_hold"] is True

    r = authed.post(url, json={"hold": False})
    assert r.json() == {"automation_hold": False}
    history = authed.get(f"/api/v1/findings/{ids['alpha']}").json()["history"]
    assert history[0]["action"] == "clear_hold"
    assert authed.post("/api/v1/findings/9999/hold", json={"hold": True}).status_code == 404
