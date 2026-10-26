import json
import logging

import httpx
import pytest

from overlaparr import logging_setup
from overlaparr.config.schema import LoggingConfig
from overlaparr.logging_setup import (
    configure_logging,
    install_secret_redaction,
    redact,
    register_secret,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    logging_setup._SECRETS.clear()
    yield
    logging_setup._SECRETS.clear()
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_overlaparr", False)]:
        root.removeHandler(h)
        h.close()


def _flush():
    for h in logging.getLogger().handlers:
        h.flush()


def test_redacts_query_token():
    out = redact("GET http://h:32400/library?X-Plex-Token=abcd1234")
    assert "X-Plex-Token=***" in out
    assert "abcd1234" not in out


@pytest.mark.parametrize(
    "text,secret",
    [
        ("X-Plex-Token: abcd1234", "abcd1234"),
        ("/movie?apikey=zzzzzz99&x=1", "zzzzzz99"),
        ("X-Api-Key: kkkk1111", "kkkk1111"),
        ("api_key=qqqq2222", "qqqq2222"),
        ("token=tttt3333", "tttt3333"),
        ("password=pppp4444", "pppp4444"),
        ("https://discord.com/api/webhooks/123456/AbC-dEf_ghi", "AbC-dEf_ghi"),
    ],
)
def test_redacts_patterns(text, secret):
    out = redact(text)
    assert secret not in out
    assert "***" in out


def test_redacts_registered_secret():
    register_secret("s3cretvalue")
    assert redact("x s3cretvalue y") == "x *** y"


def test_short_secret_not_registered():
    register_secret("abc")
    assert redact("x abc y") == "x abc y"


def test_exception_traceback_redacted(tmp_path):
    path = tmp_path / "overlaparr.log"
    configure_logging(LoggingConfig(), path)
    logger = logging.getLogger("overlaparr.test")
    try:
        raise httpx.ConnectError("http://r:7878/api/v3/movie?apikey=zzzzzz99")
    except httpx.ConnectError:
        logger.exception("failed apikey=zzzzzz99")
    _flush()
    text = path.read_text()
    assert "zzzzzz99" not in text
    assert "ConnectError" in text


def test_args_redacted(tmp_path):
    path = tmp_path / "overlaparr.log"
    configure_logging(LoggingConfig(), path)
    logging.getLogger("overlaparr.test").info("url %s", "http://h/?token=argsecret1")
    _flush()
    assert "argsecret1" not in path.read_text()


def test_rotation(tmp_path):
    path = tmp_path / "overlaparr.log"
    configure_logging(LoggingConfig(max_bytes=200, backup_count=2), path)
    logger = logging.getLogger("overlaparr.test")
    for i in range(50):
        logger.info("line number %d", i)
    _flush()
    assert (tmp_path / "overlaparr.log.1").exists()
    assert (tmp_path / "overlaparr.log.2").exists()
    assert not (tmp_path / "overlaparr.log.3").exists()


def test_json_format(tmp_path):
    path = tmp_path / "overlaparr.log"
    configure_logging(LoggingConfig(), path)
    logging.getLogger("overlaparr.test").info("hello")
    _flush()
    lines = path.read_text().strip().splitlines()
    assert lines
    for line in lines:
        data = json.loads(line)
        assert {"ts", "level", "logger", "msg"} <= data.keys()
    assert data["msg"] == "hello"
    assert data["level"] == "INFO"


def test_reconfigure_replaces_handlers(tmp_path):
    path = tmp_path / "overlaparr.log"
    configure_logging(LoggingConfig(), path)
    configure_logging(LoggingConfig(level="DEBUG"), path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_overlaparr", False)]
    assert len(ours) == 2
    logging.getLogger("overlaparr.test").debug("once")
    _flush()
    assert path.read_text().count('"once"') == 1


class _FakeStore:
    def __init__(self, values):
        self.values = values
        self.subs = []

    def secret_values(self):
        return list(self.values)

    def subscribe(self, cb):
        self.subs.append(cb)


def test_install_secret_redaction_registers_and_follows_reload():
    store = _FakeStore(["firstsecret"])
    install_secret_redaction(store)
    assert redact("a firstsecret b") == "a *** b"
    store.values.append("secondsecret")
    for cb in store.subs:
        cb(None)
    assert redact("a secondsecret b") == "a *** b"


@pytest.mark.parametrize(
    "text,secret",
    [
        ('{"apikey": "abc12345"}', "abc12345"),
        ("{'token': 'abc12345'}", "abc12345"),
        ('apikey="abc12345"', "abc12345"),
        ('{"X-Plex-Token":"abc12345","a":1}', "abc12345"),
    ],
)
def test_redacts_quoted_forms(text, secret):
    out = redact(text)
    assert secret not in out
    assert "***" in out


def test_redact_keeps_quotes():
    assert redact('{"apikey": "abc12345"}') == '{"apikey": "***"}'


def test_bad_log_args_do_not_raise(tmp_path):
    path = tmp_path / "overlaparr.log"
    configure_logging(LoggingConfig(), path)
    logging.raiseExceptions = False
    try:
        logging.getLogger("overlaparr.test").info("%d token=secret999", "x")
    finally:
        logging.raiseExceptions = True
    _flush()
    text = path.read_text()
    assert "secret999" not in text
