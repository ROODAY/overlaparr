from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from overlaparr.db.models import (
    Finding,
    FindingCopy,
    FindingServerIgnore,
    IndexItem,
    RuleState,
    TrackedEpisode,
    TrackedItem,
)
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.domain.quality import Res
from overlaparr.domain.types import ComputedCopy, ComputedFinding
from overlaparr.matching.movies import MovieIndex, match_movies
from overlaparr.scan.reconcile import ReconcileResult, keep_keys, reconcile

ONLINE = {"s1": "online", "s2": "online"}
OK = {"radarr": True}
CONF = {"radarr"}
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
PAST = NOW - timedelta(days=30)


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


def track(db, arr_id=1, instance="radarr", kind="movie", monitored=True, has_file=False):
    db.add(TrackedItem(instance_id=instance, arr_id=arr_id, kind=kind, title="X", seen_scan_id=1,
                       monitored=monitored, has_file=has_file))
    db.commit()


def copy(server="s1", quality="better", edition_cmp="unknown", matched_by="id", ep=None,
         stale=False):
    return ComputedCopy(
        server_id=server, episode_arr_id=ep, resolution=Res.R2160, hdr=True, codec="hevc",
        edition=None, size=100, quality=quality, edition_cmp=edition_cmp, matched_by=matched_by,
        stale=stale,
    )


def cf(*copies, arr_id=1, instance="radarr", kind="movie", type="wanted_elsewhere",
       relevant=None):
    return ComputedFinding(
        instance_id=instance, arr_id=arr_id, kind=kind, type=type,
        relevant_episode_ids=list(relevant or []), copies=list(copies),
    )


def run(db, computed, scan_id=1, status=ONLINE, ok=OK, conf=CONF, ignored=frozenset(), now=None):
    return reconcile(db, computed, scan_id, status, ok, set(conf), set(ignored), now=now)


def finding(db, arr_id=1, instance="radarr"):
    db.expire_all()
    return db.scalar(
        select(Finding).where(Finding.instance_id == instance, Finding.arr_id == arr_id)
    )


def copies(db, f):
    return list(db.scalars(select(FindingCopy).where(FindingCopy.finding_id == f.id)))


def test_new_finding_created(db):
    track(db)
    res = run(db, [cf(copy("s1", "better"), copy("s2", "worse", matched_by="number"))])
    f = finding(db)
    assert isinstance(res, ReconcileResult)
    assert res.new_ids == [f.id] and res.updated_ids == [] and not res.lost_ids
    assert (f.review_state, f.lifecycle, f.kind, f.type) == ("new", "active", "movie",
                                                             "wanted_elsewhere")
    assert f.first_seen_scan_id == 1 and f.last_seen_scan_id == 1
    assert (f.relevant_count, f.covered_count, f.coverage) == (1, 1, "full")
    assert f.best_quality == "better" and f.edition_summary == "unknown"
    assert f.has_number_matches is True
    cs = copies(db, f)
    assert {c.server_id for c in cs} == {"s1", "s2"}
    c1 = next(c for c in cs if c.server_id == "s1")
    assert c1.resolution == int(Res.R2160) and c1.hdr and c1.codec == "hevc" and not c1.stale


def test_series_summary_and_server_ignore(db):
    track(db, kind="series")
    run(db, [cf(copy("s1", "same", "same", ep=11), copy("s2", "worse", "different", ep=12),
                kind="series", relevant=[11, 12, 13])])
    f = finding(db)
    assert f.relevant_episode_ids == [11, 12, 13]
    assert (f.relevant_count, f.covered_count, f.coverage) == (3, 2, "partial")
    assert f.best_quality == "same" and f.edition_summary == "different"
    assert f.has_number_matches is False
    db.add(FindingServerIgnore(finding_id=f.id, server_id="s2"))
    db.commit()
    res = run(db, [cf(copy("s1", "same", ep=11), copy("s2", "worse", ep=12), copy("s2", ep=13),
                      kind="series", relevant=[11, 12, 13])], scan_id=2)
    f = finding(db)
    assert res.updated_ids == [f.id]
    assert (f.relevant_count, f.covered_count, f.coverage) == (3, 1, "partial")
    assert len(copies(db, f)) == 3  # ignored-server copies still stored
    assert f.last_seen_scan_id == 2 and f.first_seen_scan_id == 1


def test_review_state_preserved(db):
    track(db)
    run(db, [cf(copy("s1"))])
    f = finding(db)
    f.review_state = "reviewed"
    f.automation_hold = True
    db.commit()
    res = run(db, [cf(copy("s2", "worse"))], scan_id=2)
    f = finding(db)
    assert res.updated_ids == [f.id] and res.new_ids == []
    assert f.review_state == "reviewed" and f.automation_hold is True
    assert [c.server_id for c in copies(db, f)] == ["s2"]
    assert f.best_quality == "worse"


def test_ignored_persists(db):
    track(db)
    run(db, [cf(copy("s1"))])
    f = finding(db)
    f.review_state = "ignored"
    db.commit()
    run(db, [cf(copy("s1"))], scan_id=2)
    assert finding(db).review_state == "ignored"


def test_lost_only_when_all_prior_servers_reachable(db):
    track(db)
    run(db, [cf(copy("s1"), copy("s2"))])
    f = finding(db)
    f.review_state = "reviewed"
    db.commit()
    res = run(db, [], scan_id=2, status={"s1": "online", "s2": "degraded"})
    f = finding(db)
    assert res.lost_ids == [f.id] and res.resolved_ids == []
    assert f.lifecycle == "lost" and f.review_state == "reviewed"
    # last-known copies and summary kept, copies marked stale
    cs = copies(db, f)
    assert {c.server_id for c in cs} == {"s1", "s2"} and all(c.stale for c in cs)
    assert (f.covered_count, f.coverage, f.best_quality) == (1, "full", "better")


def test_offline_server_keeps_copies_stale(db):
    track(db)
    run(db, [cf(copy("s1"), copy("s2"))])
    res = run(db, [], scan_id=2, status={"s1": "online", "s2": "offline"})
    f = finding(db)
    assert res.lost_ids == [] and f.lifecycle == "active"
    cs = copies(db, f)
    assert {c.server_id for c in cs} == {"s1", "s2"} and all(c.stale for c in cs)


def test_partial_server_keeps_copies_stale(db):
    track(db)
    run(db, [cf(copy("s1"))])
    for status in ({"s1": "partial"}, {"s1": "stale"}, {}):
        res = run(db, [], scan_id=2, status=status)
        f = finding(db)
        assert res.lost_ids == [] and f.lifecycle == "active"
        assert [c.stale for c in copies(db, f)] == [True]


def test_computed_copy_from_unreachable_server_stale(db):
    track(db)
    run(db, [cf(copy("s1"), copy("s2", stale=True))], status={"s1": "online", "s2": "offline"})
    cs = {c.server_id: c.stale for c in copies(db, finding(db))}
    assert cs == {"s1": False, "s2": True}


def test_instance_failure_leaves_findings(db):
    track(db)
    run(db, [cf(copy("s1"))])
    f = finding(db)
    db.delete(db.scalar(select(TrackedItem)))
    db.commit()
    res = run(db, [], scan_id=2, ok={"radarr": False})
    f2 = finding(db)
    assert res == ReconcileResult([], [], [], [])
    assert f2.lifecycle == "active" and f2.last_seen_scan_id == 1
    assert len(copies(db, f2)) == 1 and not copies(db, f2)[0].stale
    assert f2.id == f.id


def test_removed_instance_purged(db):
    track(db)
    track(db, instance="old")
    db.add(TrackedEpisode(instance_id="old", series_arr_id=1, episode_arr_id=5, season=1,
                          episode=1, title="e"))
    db.commit()
    run(db, [cf(copy("s1")), cf(copy("s1"), instance="old")], ok={"radarr": True, "old": True},
        conf={"radarr", "old"})
    old = finding(db, instance="old")
    db.add(RuleState(rule_id="r", finding_id=old.id, coverage_hash="h", last_counted_scan_id=1))
    db.add(FindingServerIgnore(finding_id=old.id, server_id="s1"))
    db.commit()
    run(db, [cf(copy("s1"))], scan_id=2, ok={"radarr": True})
    db.expire_all()
    assert finding(db, instance="old") is None
    assert finding(db) is not None
    assert db.scalars(select(TrackedItem.instance_id)).all() == ["radarr"]
    assert db.scalars(select(TrackedEpisode)).all() == []
    assert db.scalars(select(RuleState)).all() == []
    assert db.scalars(select(FindingServerIgnore)).all() == []
    assert {c.finding_id for c in db.scalars(select(FindingCopy))} == {finding(db).id}


def test_item_deleted_marks_removed(db):
    track(db)
    run(db, [cf(copy("s1"))])
    f = finding(db)
    f.review_state = "reviewed"
    db.delete(db.scalar(select(TrackedItem)))
    db.commit()
    res = run(db, [], scan_id=2)
    f = finding(db)
    assert res.removed_ids == [f.id] and res.lost_ids == []
    assert f.lifecycle == "removed" and f.review_state == "reviewed"


def test_lost_reactivates_when_copy_returns(db):
    track(db)
    run(db, [cf(copy("s1"))])
    run(db, [], scan_id=2)
    assert finding(db).lifecycle == "lost"
    res = run(db, [cf(copy("s2"))], scan_id=3)
    f = finding(db)
    assert f.lifecycle == "active" and res.updated_ids == [f.id] and res.new_ids == []
    assert [c.server_id for c in copies(db, f)] == ["s2"]


def test_ignore_tag_sets_ignored(db):
    track(db)
    track(db, arr_id=2)
    run(db, [cf(copy("s1")), cf(copy("s1"), arr_id=2)])
    f2 = finding(db, arr_id=2)
    f2.review_state = "reviewed"
    db.commit()
    # matching skips tagged items, so the tagged item has no computed finding
    res = run(db, [cf(copy("s1"))], scan_id=2, ignored={("radarr", 2)})
    f2 = finding(db, arr_id=2)
    assert f2.review_state == "ignored" and f2.lifecycle == "active"
    assert res.lost_ids == [] and res.resolved_ids == []
    assert [(c.server_id, c.stale) for c in copies(db, f2)] == [("s1", False)]
    assert f2.covered_count == 1
    assert finding(db).review_state == "new"


def test_ignore_tag_on_new_computed_finding(db):
    track(db)
    run(db, [cf(copy("s1"))], ignored={("radarr", 1)})
    assert finding(db).review_state == "ignored"


def test_user_unmonitored_wanted_resolved_not_lost(db):
    track(db)
    run(db, [cf(copy("s1"))])
    db.scalar(select(TrackedItem)).monitored = False
    db.commit()
    res = run(db, [], scan_id=2)
    f = finding(db)
    assert res.resolved_ids == [f.id] and res.lost_ids == []
    assert f.lifecycle == "resolved"
    assert [c.stale for c in copies(db, f)] == [False]


def test_downloaded_wanted_resolved(db):
    track(db)
    run(db, [cf(copy("s1"))])
    db.scalar(select(TrackedItem)).has_file = True
    db.commit()
    res = run(db, [], scan_id=2)
    assert res.resolved_ids == [finding(db).id] and finding(db).lifecycle == "resolved"


def test_actioned_downloaded_wanted_resolved(db):
    track(db)
    run(db, [cf(copy("s1"))])
    f = finding(db)
    f.review_state = "actioned"
    item = db.scalar(select(TrackedItem))
    item.has_file, item.monitored = True, False
    db.commit()
    res = run(db, [], scan_id=2)
    assert res.resolved_ids == [finding(db).id]


def test_on_disk_file_deleted_resolved(db):
    track(db, has_file=True)
    run(db, [cf(copy("s1"), type="on_disk")])
    db.scalar(select(TrackedItem)).has_file = False
    db.commit()
    res = run(db, [], scan_id=2)
    assert res.resolved_ids == [finding(db).id]


def test_resolved_reactivates_when_computed_again(db):
    track(db)
    run(db, [cf(copy("s1"))])
    db.scalar(select(TrackedItem)).monitored = False
    db.commit()
    run(db, [], scan_id=2)
    db.scalar(select(TrackedItem)).monitored = True
    db.commit()
    res = run(db, [cf(copy("s1"))], scan_id=3)
    f = finding(db)
    assert f.lifecycle == "active" and res.updated_ids == [f.id]


def _match(db):
    keep, _ = keep_keys(db)
    items = list(db.scalars(select(TrackedItem)))
    rows = [IndexItem(server_id="s1", kind="movie", rating_key="k", title="X", tmdb=10,
                      resolution=int(Res.R2160))]
    return match_movies(items, MovieIndex.build(rows), ONLINE, None, keep=keep)


def test_actioned_unmonitored_by_us_stays_active_via_keep(db):
    db.add(TrackedItem(instance_id="radarr", arr_id=1, kind="movie", title="X", tmdb=10,
                       monitored=True, seen_scan_id=1))
    db.commit()
    run(db, _match(db))
    f = finding(db)
    f.review_state = "actioned"  # our action unmonitored it
    db.scalar(select(TrackedItem)).monitored = False
    db.commit()
    computed = _match(db)
    assert len(computed) == 1
    res = run(db, computed, scan_id=2)
    f = finding(db)
    assert f.lifecycle == "active" and res.updated_ids == [f.id]
    assert res.lost_ids == [] and res.resolved_ids == []


def test_actioned_copies_gone_lost_with_copies_retained(db):
    track(db)
    run(db, [cf(copy("s1"))])
    f = finding(db)
    f.review_state = "actioned"
    db.scalar(select(TrackedItem)).monitored = False
    db.commit()
    res = run(db, [], scan_id=2)
    f = finding(db)
    assert res.lost_ids == [f.id] and f.lifecycle == "lost"
    assert f.review_state == "actioned"
    assert [(c.server_id, c.stale) for c in copies(db, f)] == [("s1", True)]


def _episode(eid, monitored=True, has_file=False, air=PAST):
    return TrackedEpisode(instance_id="sonarr", series_arr_id=5, episode_arr_id=eid, season=1,
                          episode=eid, title="e", monitored=monitored, has_file=has_file,
                          air_date_utc=air)


def test_series_wanted_resolved_vs_lost(db):
    track(db, arr_id=5, instance="sonarr", kind="series")
    db.add_all([_episode(1), _episode(2)])
    db.commit()
    ok, conf = {"sonarr": True}, {"sonarr"}
    run(db, [cf(copy("s1", ep=1), instance="sonarr", arr_id=5, kind="series", relevant=[1, 2])],
        ok=ok, conf=conf)
    # episode 2 still wanted, copies gone -> lost
    res = run(db, [], scan_id=2, ok=ok, conf=conf, now=NOW)
    f = finding(db, arr_id=5, instance="sonarr")
    assert res.lost_ids == [f.id]
    # back to active, then nothing wanted any more -> resolved
    run(db, [cf(copy("s1", ep=1), instance="sonarr", arr_id=5, kind="series", relevant=[1, 2])],
        scan_id=3, ok=ok, conf=conf)
    for e in db.scalars(select(TrackedEpisode)):
        e.monitored = False
    db.commit()
    res = run(db, [], scan_id=4, ok=ok, conf=conf, now=NOW)
    assert res.resolved_ids == [f.id]


def test_keep_keys(db):
    track(db)
    track(db, arr_id=2)
    track(db, arr_id=3)
    run(db, [cf(copy("s1")), cf(copy("s1"), arr_id=2), cf(copy("s1"), arr_id=3, kind="series",
                                                           relevant=[7, 8])])
    for arr_id, lifecycle in ((1, "active"), (2, "removed"), (3, "lost")):
        f = finding(db, arr_id=arr_id)
        f.review_state, f.lifecycle = "actioned", lifecycle
        db.commit()
    keys, relevant = keep_keys(db)
    assert keys == {("radarr", 1, "wanted_elsewhere"), ("radarr", 3, "wanted_elsewhere")}
    assert relevant[("radarr", 3, "wanted_elsewhere")] == [7, 8]
