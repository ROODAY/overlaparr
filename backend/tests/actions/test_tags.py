import json

import httpx
import pytest
import respx

from overlaparr.actions.tags import sync_tags
from overlaparr.arr.radarr import RadarrClient
from overlaparr.config.schema import AppConfig
from overlaparr.db.models import Finding, FindingCopy, FindingServerIgnore, Server, TrackedItem
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.scan.engine import ScanContext
from overlaparr.scan.reconcile import ReconcileResult
from tests.actions.helpers import R, RADARR

TAGS = [{"id": 1, "label": "dupe-elsewhere"}, {"id": 2, "label": "dupe-partial"},
        {"id": 3, "label": "dupe-ignore"}, {"id": 9, "label": "favorite"},
        {"id": 10, "label": "on-alpha"}]


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        s.add(Server(machine_id="A", name="Alpha"))
        s.commit()
        yield s
    eng.dispose()


def ctx_for(db, ok=True, enabled=True, dry_run=False, per_server=False, servers=()):
    cfg = AppConfig(instances=[RADARR], tags={"enabled": enabled, "per_server_tags": per_server},
                    servers=list(servers))
    client = RadarrClient(R, "radarrkey123")
    return ScanContext(1, db, cfg, {}, {"r1": ok}, ReconcileResult(), {"r1": client}, None,
                       dry_run)


def item(db, arr_id=1, tags=(), finding=True, stale=False, **fk):
    db.add(TrackedItem(instance_id="r1", arr_id=arr_id, kind="movie", title=f"M{arr_id}",
                       seen_scan_id=1, tag_ids=list(tags)))
    if finding:
        f = Finding(instance_id="r1", arr_id=arr_id, kind="movie", type="on_disk",
                    coverage=fk.pop("coverage", "full"), best_quality="x", edition_summary="x",
                    first_seen_scan_id=1, last_seen_scan_id=1, **fk)
        db.add(f)
        db.flush()
        db.add(FindingCopy(finding_id=f.id, server_id="A", quality="same",
                           edition_cmp="same", matched_by="id", stale=stale))
    db.commit()


def mock(tags=TAGS):
    respx.get(f"{R}/api/v3/tag").respond(json=tags)
    return respx.put(f"{R}/api/v3/movie/editor").respond(202)


def bodies(route):
    return [json.loads(c.request.content) for c in route.calls]


def tag_of(db, arr_id=1):
    return db.query(TrackedItem).filter_by(arr_id=arr_id).one().tag_ids


@respx.mock
def test_adds_dupe_tag(db):
    item(db)
    route = mock()
    sync_tags(ctx_for(db))
    assert bodies(route) == [{"movieIds": [1], "tags": [1], "applyTags": "add"}]
    assert tag_of(db) == [1]


@respx.mock
def test_creates_missing_tag_and_adds_partial(db):
    item(db, coverage="partial")
    respx.get(f"{R}/api/v3/tag").respond(json=[])

    def create(request):
        label = json.loads(request.content)["label"]
        return httpx.Response(200, json={"id": 7 if label == "dupe-partial" else 6,
                                         "label": label})

    respx.post(f"{R}/api/v3/tag").mock(side_effect=create)
    route = respx.put(f"{R}/api/v3/movie/editor").respond(202)
    sync_tags(ctx_for(db))
    assert sorted(b["tags"][0] for b in bodies(route)) == [6, 7]


@respx.mock
def test_removes_tool_tag_when_finding_removed(db):
    item(db, tags=[1, 2, 9], lifecycle="removed")
    route = mock()
    sync_tags(ctx_for(db))
    assert sorted(b["tags"][0] for b in bodies(route)) == [1, 2]
    assert all(b["applyTags"] == "remove" for b in bodies(route))
    assert tag_of(db) == [9]


@respx.mock
def test_never_removes_ignore_tag(db):
    item(db, tags=[3], finding=False)
    route = mock()
    sync_tags(ctx_for(db))
    assert route.call_count == 0


@respx.mock
def test_never_removes_foreign_tag(db):
    # "on-mine" is not a known server label, so it is the user's tag
    item(db, tags=[9, 11], finding=False)
    route = mock([*TAGS, {"id": 11, "label": "on-mine"}])
    sync_tags(ctx_for(db, per_server=True))
    assert route.call_count == 0


@respx.mock
def test_stale_copies_keep_tag(db):
    item(db, tags=[1], stale=True)
    route = mock()
    sync_tags(ctx_for(db))
    assert route.call_count == 0
    assert tag_of(db) == [1]


@respx.mock
def test_ignored_server_copy_does_not_support(db):
    item(db, tags=[1])
    db.add(FindingServerIgnore(finding_id=1, server_id="A"))
    db.commit()
    route = mock()
    sync_tags(ctx_for(db))
    assert bodies(route) == [{"movieIds": [1], "tags": [1], "applyTags": "remove"}]


@respx.mock
def test_per_server_tag_added_and_unsupported_removed(db):
    item(db, tags=[1])
    db.add(Server(machine_id="B", name="Beta"))
    db.commit()
    route = mock([*TAGS, {"id": 12, "label": "on-beta"}])
    db.query(TrackedItem).one().tag_ids = [1, 12]
    db.commit()
    sync_tags(ctx_for(db, per_server=True))
    got = {(b["tags"][0], b["applyTags"]) for b in bodies(route)}
    assert got == {(10, "add"), (12, "remove")}


@respx.mock
def test_per_server_label_override_normalized(db):
    item(db)
    mock([])
    respx.post(f"{R}/api/v3/tag").mock(side_effect=lambda r: httpx.Response(
        200, json={"id": 20 if b"on-big-box" in r.content else 6,
                   "label": json.loads(r.content)["label"]}))
    sync_tags(ctx_for(db, per_server=True, servers=[{"id": "A", "label": "Big  Box!"}]))
    posted = [json.loads(c.request.content)["label"] for c in respx.calls
              if c.request.method == "POST"]
    assert sorted(posted) == ["dupe-elsewhere", "on-big-box"]


@respx.mock
def test_failed_instance_skipped(db):
    item(db)
    route = mock()
    sync_tags(ctx_for(db, ok=False))
    assert route.call_count == 0


@respx.mock
def test_disabled_no_calls(db):
    item(db)
    mock()
    sync_tags(ctx_for(db, enabled=False))
    sync_tags(ctx_for(db, dry_run=True))
    assert len(respx.calls) == 0


@respx.mock
def test_arr_error_is_swallowed(db):
    item(db)
    respx.get(f"{R}/api/v3/tag").respond(json=TAGS)
    respx.put(f"{R}/api/v3/movie/editor").respond(500)
    sync_tags(ctx_for(db))
    assert tag_of(db) == []


@respx.mock
def test_batches_items_per_tag(db):
    item(db, 1)
    item(db, 2)
    route = mock()
    sync_tags(ctx_for(db))
    assert bodies(route) == [{"movieIds": [1, 2], "tags": [1], "applyTags": "add"}]
