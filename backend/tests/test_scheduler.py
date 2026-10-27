from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from overlaparr import scheduler as sched_mod
from overlaparr.config.schema import AppConfig
from overlaparr.db.models import ActionLog, NotificationLog, PendingEvent, utcnow
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.scheduler import AppScheduler, next_runs, run_retention


def conn(cid, enabled=True, **digest):
    return {"id": cid, "name": cid, "type": "webhook", "url": "https://x.test/h",
            "enabled": enabled, "digest": digest}


def cfg_of(**kw):
    return AppConfig.model_validate(kw)


class FakeStore:
    def __init__(self, cfg):
        self.current = cfg
        self.subs = []

    def subscribe(self, cb):
        self.subs.append(cb)


@pytest.fixture
def dbf(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    yield make_sessionmaker(eng)
    eng.dispose()


@pytest.fixture
def make(dbf):
    made = []

    def _make(cfg):
        store = FakeStore(cfg)
        engine = MagicMock()
        s = AppScheduler(store, engine, dbf)
        made.append(s)
        return s, store, engine

    yield _make
    for s in made:
        s.shutdown()


def fields(trigger):
    return {f.name: str(f) for f in trigger.fields}


def test_scan_job_uses_cron(make):
    s, store, engine = make(cfg_of(scan={"cron": "15 4 * * *"}))
    s.start()
    job = s.scheduler.get_job("scan")
    f = fields(job.trigger)
    assert (f["minute"], f["hour"]) == ("15", "4")
    assert job.max_instances == 1 and job.coalesce is True
    assert job.misfire_grace_time == 300
    job.func()
    engine.start.assert_called_once_with("schedule")


def test_reapply_on_config_change(make):
    s, store, _ = make(cfg_of(scan={"cron": "0 1 * * *"}))
    s.start()
    assert store.subs == [s.apply]
    new = cfg_of(scan={"cron": "30 2 * * *"}, notifications={"connections": [
        conn("a", mode="daily")]})
    store.subs[0](new)
    assert fields(s.scheduler.get_job("scan").trigger)["minute"] == "30"
    assert s.scheduler.get_job("digest:a") is not None
    store.subs[0](cfg_of())
    assert s.scheduler.get_job("digest:a") is None
    assert s.scheduler.get_job("scan") is not None


def test_digest_job_per_connection(make, monkeypatch):
    cfg = cfg_of(notifications={"app_url": "https://o.test", "connections": [
        conn("d", mode="daily", time="07:45"),
        conn("w", mode="weekly", time="08:05", weekday=2),
        conn("c", mode="cron", cron="0 12 * * 1"),
        conn("off", mode="off"),
        conn("disabled", enabled=False, mode="daily"),
    ]})
    s, store, _ = make(cfg)
    s.start()
    ids = {j.id for j in s.scheduler.get_jobs()}
    assert ids == {"scan", "retention", "digest:d", "digest:w", "digest:c"}
    d = fields(s.scheduler.get_job("digest:d").trigger)
    assert (d["hour"], d["minute"]) == ("7", "45")
    w = fields(s.scheduler.get_job("digest:w").trigger)
    assert (w["hour"], w["minute"], w["day_of_week"]) == ("8", "5", "2")
    assert fields(s.scheduler.get_job("digest:c").trigger)["hour"] == "12"

    sent = []
    monkeypatch.setattr(sched_mod, "send_digest",
                        lambda db, c, app_url=None: sent.append((c.id, app_url)))
    job = s.scheduler.get_job("digest:d")
    job.func(*job.args)
    assert sent == [("d", "https://o.test")]
    # connection disabled at fire time -> skipped
    store.current = cfg_of(notifications={"connections": [
        conn("d", enabled=False, mode="daily")]})
    job.func(*job.args)
    assert len(sent) == 1


def test_next_runs():
    runs = next_runs("0 */6 * * *", 3)
    assert len(runs) == 3
    assert runs[1] - runs[0] == timedelta(hours=6)
    assert all(r.minute == 0 for r in runs)
    assert len(next_runs("0 * * * *")) == 5
    with pytest.raises(ValueError):
        next_runs("not a cron")


def test_invalid_cron_rejected_by_schema():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"scan": {"cron": "bogus"}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"notifications": {"connections": [
            conn("a", mode="cron", cron="99 99 * * *")]}})
    AppConfig.model_validate({"scan": {"cron": "*/5 * * * *"}})


def test_retention_job(make, dbf):
    now = utcnow()
    old, new = now - timedelta(days=100), now - timedelta(days=5)

    def al(ts):
        return ActionLog(ts=ts, actor="ui", instance_id="i", instance_name="i", arr_id=1,
                         item_title="t", action="a", result="ok")

    with dbf() as db:
        db.add_all([al(old), al(new), NotificationLog(ts=old, connection_id="c", event="e", success=True),
                    NotificationLog(ts=new, connection_id="c", event="e", success=True),
                    PendingEvent(ts=old, kind="k", sent=True), PendingEvent(ts=old, kind="k", sent=False),
                    PendingEvent(ts=new, kind="k", sent=True)])
        db.commit()
        counts = run_retention(db, AppConfig.model_validate({"action_log": {"retention_days": 30}}), now)
        assert counts == {"action_log": 1, "notification_log": 1, "pending_events": 1}
        assert len(db.scalars(select(ActionLog)).all()) == 1
        assert len(db.scalars(select(NotificationLog)).all()) == 1
        assert len(db.scalars(select(PendingEvent)).all()) == 2
        # retention_days None keeps action log
        counts = run_retention(db, AppConfig(), now + timedelta(days=1000))
        assert counts["action_log"] == 0
        assert len(db.scalars(select(ActionLog)).all()) == 1

    s, *_ = make(AppConfig())
    job = None
    s.start()
    job = s.scheduler.get_job("retention")
    f = fields(job.trigger)
    assert (f["hour"], f["minute"]) == ("3", "30")
