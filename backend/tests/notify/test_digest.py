from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from overlaparr.config.schema import WebhookConn
from overlaparr.db.models import (
    ActionLog,
    Finding,
    NotificationLog,
    ScanRun,
    Server,
    TrackedEpisode,
    TrackedItem,
    utcnow,
)
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.notify import channels
from overlaparr.notify.digest import build_digest, send_digest
from overlaparr.stats import format_bytes, reclaimable_bytes

GB = 1024**3


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


def finding(db, arr_id, kind="movie", ftype="on_disk", size=None, ep_ids=(), **kw):
    db.add(TrackedItem(instance_id="i", arr_id=arr_id, kind=kind, title=f"T{arr_id}",
                       seen_scan_id=1, file_size=size))
    f = Finding(instance_id="i", arr_id=arr_id, kind=kind, type=ftype, coverage="full",
                best_quality="better", edition_summary="same", first_seen_scan_id=1,
                last_seen_scan_id=1, relevant_episode_ids=list(ep_ids), **kw)
    db.add(f)
    db.commit()
    return f


def seed(db):
    finding(db, 1, size=2 * GB)
    finding(db, 2, size=5 * GB, review_state="ignored")
    finding(db, 3, size=7 * GB, lifecycle="lost")
    finding(db, 4, ftype="wanted_elsewhere", size=9 * GB)
    finding(db, 5, kind="series", ep_ids=[501, 502])
    for ep, size in ((501, GB), (502, 2 * GB), (503, 50 * GB)):
        db.add(TrackedEpisode(instance_id="i", series_arr_id=5, episode_arr_id=ep, season=1,
                              episode=1, title="e", file_size=size))
    db.commit()


def test_digest_totals_and_reclaimable(db):
    seed(db)
    db.add(Server(machine_id="A", name="Alpha", reachability="online"))
    db.add(Server(machine_id="B", name="Beta", reachability="offline"))
    db.add(Server(machine_id="H", name="Home", reachability="offline", is_home=True))
    db.add(ActionLog(actor="auto", instance_id="i", instance_name="I", arr_id=1,
                     item_title="T1", action="unmonitor", result="ok"))
    db.add(ActionLog(actor="ui", instance_id="i", instance_name="I", arr_id=1,
                     item_title="T1", action="unmonitor", result="ok"))
    db.commit()
    assert reclaimable_bytes(db) == 5 * GB  # 2 GB movie + 1 + 2 GB episodes
    m = build_digest(db, utcnow() - timedelta(days=1), None)
    assert m.title == "Overlaparr digest"
    assert m.payload["reclaimable_bytes"] == 5 * GB
    assert "Reclaimable space: 5.0 GB" in m.text
    assert "Active findings: 4" in m.text
    assert "on_disk: 3 (3 full)" in m.text and "wanted_elsewhere: 1 (1 full)" in m.text
    assert "Lost findings: 1" in m.text
    assert "Automation actions: 1" in m.text
    assert "Servers healthy: 1/2" in m.text and "Beta: offline" in m.text
    assert "Open Overlaparr" not in m.text


def test_digest_new_since(db):
    seed(db)
    old = ScanRun(status="ok", trigger="manual", started_at=utcnow() - timedelta(days=10))
    recent = ScanRun(status="ok", trigger="manual", started_at=utcnow())
    db.add_all([old, recent])
    db.commit()
    for f in db.scalars(select(Finding)):
        f.first_seen_scan_id = old.id
    db.get(Finding, 1).first_seen_scan_id = recent.id
    db.commit()
    assert build_digest(db, utcnow() - timedelta(days=1), None).payload["new"] == 1


def test_digest_links_use_app_url(db):
    seed(db)
    m = build_digest(db, utcnow() - timedelta(days=1), "https://ovl.test/")
    assert "https://ovl.test/findings" in m.text
    assert 'href="https://ovl.test/findings?type=on_disk"' in m.html
    assert "//findings" not in m.text


def test_send_digest_since_defaults(db, monkeypatch):
    sent = []
    monkeypatch.setattr(channels, "send", lambda c, m: sent.append(m))
    conn = WebhookConn(id="w", name="w", type="webhook", url="https://x.test/h")
    assert send_digest(db, conn) is True
    first = datetime.fromisoformat(sent[0].payload["since"])
    assert utcnow() - timedelta(days=7, minutes=1) < first < utcnow() - timedelta(days=6, hours=23)
    [log] = db.scalars(select(NotificationLog))
    assert log.event == "digest" and log.success
    send_digest(db, conn)
    assert datetime.fromisoformat(sent[1].payload["since"]) >= log.ts - timedelta(seconds=1)


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(int(1.8 * 1024**4)) == "1.8 TB"
