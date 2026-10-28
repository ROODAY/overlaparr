import pytest
from pydantic import TypeAdapter, ValidationError

from overlaparr.config.schema import (
    AppConfig,
    DigestSchedule,
    DiscordConn,
    InstanceConfig,
    LoggingConfig,
    NotificationConnection,
    Rule,
    ScanConfig,
    TagsConfig,
    new_id,
)


def test_defaults():
    c = AppConfig()
    assert c.scan.cron == "0 */6 * * *"
    assert c.scan.server_timeout_seconds == 10
    assert c.scan.stale_after_hours == 48
    assert c.tags.enabled is False
    assert c.tags.ignore == "dupe-ignore"
    assert c.logging.max_bytes == 10485760
    assert c.logging.backup_count == 5
    assert c.rules == []


def test_rule_defaults():
    r = Rule(id="r", name="n")
    assert r.enabled is False
    assert r.action == "unmonitor"
    assert r.finding_types == ["wanted_elsewhere"]
    assert r.coverage == "full"
    assert r.quality == ["better", "same"]
    assert r.edition == ["same"]
    assert r.match_methods == ["id"]
    assert r.wait_scans == 2
    assert r.min_servers == 1


def test_enabled_delete_requires_confirmation():
    with pytest.raises(ValidationError):
        Rule(id="r", name="n", action="delete", enabled=True)
    Rule(id="r", name="n", action="delete", enabled=True, delete_confirmed=True)


def test_bad_tag_name_rejected():
    with pytest.raises(ValidationError):
        TagsConfig(dupe="Dupe Elsewhere")


def _inst(i, name):
    return {"id": i, "name": name, "type": "sonarr", "url": "http://x:8989", "api_key": "k"}


def test_duplicate_instance_names_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"instances": [_inst("a", "S"), _inst("b", "S")]})
    c = AppConfig.model_validate({"instances": [_inst("a", "S"), _inst("b", "T")]})
    assert isinstance(c.instances[0], InstanceConfig)


def test_duplicate_ids_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"instances": [_inst("a", "S"), _inst("a", "T")]})
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"rules": [{"id": "r", "name": "a"}, {"id": "r", "name": "b"}]}
        )
    d = {"type": "discord", "name": "d", "webhook_url": "http://w"}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"notifications": {"connections": [{**d, "id": "n"}, {**d, "id": "n"}]}}
        )


def test_min_servers_and_wait_scans_validated():
    with pytest.raises(ValidationError):
        Rule(id="r", name="n", min_servers=0)
    with pytest.raises(ValidationError):
        Rule(id="r", name="n", wait_scans=0)


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"bogus": 1})


def test_notification_connection_discriminated():
    c = TypeAdapter(NotificationConnection).validate_python(
        {"type": "discord", "id": "n", "name": "d", "webhook_url": "http://w"}
    )
    assert isinstance(c, DiscordConn)
    assert c.enabled is False
    flags = [v for v in c.events.model_dump().values() if isinstance(v, bool)]
    assert flags and not any(flags)


def test_new_id():
    assert len(new_id()) == 12
    assert new_id() != new_id()


def test_tag_name_rejects_trailing_newline():
    with pytest.raises(ValidationError):
        TagsConfig(dupe="dupe\n")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"weekday": 7},
        {"weekday": -1},
        {"time": "24:00"},
        {"time": "9:00"},
        {"time": "09:60"},
        {"time": "09:00\n"},
        {"mode": "cron"},
    ],
)
def test_digest_schedule_rejects_invalid(kwargs):
    with pytest.raises(ValidationError):
        DigestSchedule(**kwargs)


def test_digest_schedule_accepts_valid():
    DigestSchedule(mode="weekly", weekday=6, time="23:59")
    DigestSchedule(mode="cron", cron="0 9 * * 1")


@pytest.mark.parametrize(
    "field", ["server_timeout_seconds", "server_index_budget_seconds", "page_size", "stale_after_hours"]
)
def test_scan_numbers_must_be_positive(field):
    with pytest.raises(ValidationError):
        ScanConfig(**{field: 0})


def test_logging_bounds_and_level():
    with pytest.raises(ValidationError):
        LoggingConfig(max_bytes=0)
    with pytest.raises(ValidationError):
        LoggingConfig(backup_count=-1)
    with pytest.raises(ValidationError):
        LoggingConfig(level="VERBOSE")
    LoggingConfig(level="DEBUG", backup_count=0)


@pytest.mark.parametrize("kw", [{"ignore": "dupe-elsewhere"}, {"partial": "dupe-elsewhere"},
                                {"ignore": "dupe-partial"}])
def test_tag_names_must_be_distinct(kw):
    with pytest.raises(ValidationError, match="must be different"):
        TagsConfig(**kw)
