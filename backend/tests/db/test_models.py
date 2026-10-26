from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from overlaparr.db import models
from overlaparr.db.session import init_db, make_engine, make_sessionmaker

EXPECTED_TABLES = {
    "app_state", "users", "servers", "index_items", "index_episodes",
    "tracked_items", "tracked_episodes", "findings", "finding_copies",
    "finding_server_ignores", "rule_states", "scan_runs", "action_log",
    "notification_log", "pending_events",
}


@pytest.fixture
def engine(tmp_path: Path):
    eng = make_engine(tmp_path / "db" / "overlaparr.db")
    init_db(eng)
    yield eng
    eng.dispose()


def _finding(**kw):
    base = dict(
        instance_id="i1", arr_id=1, kind="movie", type="wanted_elsewhere",
        review_state="new", lifecycle="active", automation_hold=False,
        relevant_count=1, covered_count=1, coverage="full", best_quality="1080p",
        edition_summary="", has_number_matches=False, relevant_episode_ids=[],
        first_seen_scan_id=1, last_seen_scan_id=1,
    )
    base.update(kw)
    return models.Finding(**base)


def test_init_db_creates_tables(engine):
    names = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= names
    assert "alembic_version" in names


def test_init_db_idempotent(engine):
    init_db(engine)


def test_wal_enabled(engine):
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_finding_unique_per_type(engine):
    Session = make_sessionmaker(engine)
    with Session() as s:
        s.add_all([_finding(type="wanted_elsewhere"), _finding(type="on_disk")])
        s.commit()
        s.add(_finding(type="on_disk"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_copy_cascade_delete(engine):
    Session = make_sessionmaker(engine)
    with Session() as s:
        f = _finding()
        s.add(f)
        s.flush()
        s.add(models.FindingCopy(
            finding_id=f.id, server_id="m1", hdr=False, quality="better",
            edition_cmp="same", matched_by="id", stale=False,
        ))
        s.add(models.FindingServerIgnore(finding_id=f.id, server_id="m1"))
        s.add(models.RuleState(
            rule_id="r", finding_id=f.id, consecutive_scans=1,
            coverage_hash="h", last_counted_scan_id=1,
        ))
        s.commit()
        s.delete(f)
        s.commit()
        assert s.query(models.FindingCopy).count() == 0
        assert s.query(models.FindingServerIgnore).count() == 0
        assert s.query(models.RuleState).count() == 0


def test_db_level_cascade(engine):
    Session = make_sessionmaker(engine)
    with Session() as s:
        f = _finding()
        s.add(f)
        s.flush()
        s.add(models.FindingCopy(finding_id=f.id, server_id="m1", hdr=False,
                                 quality="same", edition_cmp="same",
                                 matched_by="id", stale=False))
        s.commit()
        s.execute(text("DELETE FROM findings"))
        s.commit()
        assert s.query(models.FindingCopy).count() == 0


def test_timestamps_roundtrip_utc(engine):
    from datetime import UTC, datetime
    Session = make_sessionmaker(engine)
    with Session() as s:
        s.add(models.ScanRun(status="running", trigger="manual", summary={}))
        s.commit()
        run = s.query(models.ScanRun).one()
        assert run.started_at.tzinfo is not None
        assert abs((datetime.now(UTC) - run.started_at).total_seconds()) < 60
