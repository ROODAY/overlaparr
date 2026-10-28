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


def test_finding_ids_never_reused(engine):
    Session = make_sessionmaker(engine)
    with Session() as s:
        s.add(_finding(arr_id=1))
        s.add(_finding(arr_id=2))
        s.commit()
        top = s.query(models.Finding).order_by(models.Finding.id.desc()).first().id
        s.execute(text("DELETE FROM findings"))
        s.commit()
        f = _finding(arr_id=3)
        s.add(f)
        s.commit()
        assert f.id > top


def _alembic_cfg(conn):
    from alembic.config import Config

    from overlaparr.db.session import MIGRATIONS_DIR

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["connection"] = conn
    return cfg


def test_migrations_match_models(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True,
                                                     "render_as_batch": True})
        assert compare_metadata(ctx, models.Base.metadata) == []


def test_autoincrement_migration_keeps_rows_and_children(tmp_path: Path):
    from alembic import command

    eng = make_engine(tmp_path / "old.db")
    with eng.begin() as conn:
        command.upgrade(_alembic_cfg(conn), "init")
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO findings (id, instance_id, arr_id, kind, type, review_state, lifecycle,"
            " automation_hold, relevant_count, covered_count, relevant_episode_ids, coverage,"
            " best_quality, edition_summary, has_number_matches, first_seen_scan_id,"
            " last_seen_scan_id, updated_at) VALUES (3, 'i1', 1, 'movie', 'on_disk', 'new',"
            " 'active', 0, 1, 1, '[]', 'full', 'same', 'same', 0, 1, 1,"
            " '2026-10-26 00:00:00.000000')"))
        conn.execute(text(
            "INSERT INTO finding_copies (finding_id, server_id, hdr, quality, edition_cmp,"
            " matched_by, stale) VALUES (3, 'm1', 0, 'same', 'same', 'id', 0)"))
        conn.execute(text("INSERT INTO finding_server_ignores (finding_id, server_id)"
                          " VALUES (3, 'm2')"))
        conn.execute(text(
            "INSERT INTO rule_states (rule_id, finding_id, consecutive_scans, coverage_hash,"
            " last_counted_scan_id) VALUES ('r', 3, 1, 'h', 1)"))
        conn.execute(text(
            "INSERT INTO action_log (ts, actor, instance_id, instance_name, finding_id, arr_id,"
            " item_title, action, scope, prior_state, result, undoable) VALUES"
            " ('2026-10-26 00:00:00.000000', 'ui', 'i1', 'I', 9, 1, 'T', 'unmonitor', '{}',"
            " '{}', 'ok', 0)"))
    init_db(eng)
    Session = make_sessionmaker(eng)
    with Session() as s:
        assert [f.id for f in s.query(models.Finding)] == [3]
        assert s.query(models.FindingCopy).count() == 1
        assert s.query(models.FindingServerIgnore).count() == 1
        assert s.query(models.RuleState).count() == 1
        # a new finding never takes an id an old action-log row still points at
        f = _finding(arr_id=2)
        s.add(f)
        s.commit()
        assert f.id > 9
        s.execute(text("DELETE FROM findings WHERE id = 3"))
        s.commit()
        assert s.query(models.FindingCopy).count() == 0  # FK cascade still in place
    with eng.connect() as conn:
        sql = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name = 'findings'")).scalar()
        assert "AUTOINCREMENT" in sql.upper()
    eng.dispose()
