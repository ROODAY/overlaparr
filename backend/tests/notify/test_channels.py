import json
import ssl
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from sqlalchemy import select

from overlaparr.config.schema import AppriseConn, DiscordConn, EmailConn, WebhookConn
from overlaparr.db.models import NotificationLog
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.notify import channels
from overlaparr.notify.channels import Message, NotifyError, send, send_with_retry

HOOK = "https://discord.com/api/webhooks/123456/abcDEF-secret_token"


def msg(**kw):
    base = dict(title="Title", text="Body text", html=None, payload={"event": "new_findings"})
    base.update(kw)
    return Message(**base)


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s


@respx.mock
def test_discord_payload_truncated():
    route = respx.post(HOOK).respond(204)
    send(DiscordConn(id="d", name="d", type="discord", webhook_url=HOOK), msg(text="x" * 5000))
    content = json.loads(route.calls[0].request.content)["content"]
    assert len(content) == 2000 and content.endswith("…")
    assert content.startswith("**Title**\n")


@respx.mock
def test_discord_short_message_not_truncated():
    route = respx.post(HOOK).respond(204)
    send(DiscordConn(id="d", name="d", type="discord", webhook_url=HOOK), msg())
    assert json.loads(route.calls[0].request.content) == {"content": "**Title**\nBody text"}


@respx.mock
def test_webhook_payload_shape():
    route = respx.put("https://example.com/hook").respond(200)
    conn = WebhookConn(
        id="w", name="w", type="webhook", url="https://example.com/hook",
        method="PUT", headers={"X-Foo": "bar"},
    )
    send(conn, msg(payload={"event": "e1", "n": 2}))
    req = route.calls[0].request
    assert req.headers["X-Foo"] == "bar"
    assert json.loads(req.content) == {
        "event": "e1", "title": "Title", "text": "Body text",
        "data": {"event": "e1", "n": 2},
    }


def _email(tls):
    return EmailConn(
        id="e", name="e", type="email", host="smtp.test", port=25, tls=tls,
        username="u", password="pw-secret-1", from_addr="a@x.com", to=["b@x.com", "c@x.com"],
    )


def test_email_multipart(monkeypatch):
    smtp = MagicMock()
    cls = MagicMock()
    cls.return_value.__enter__.return_value = smtp
    monkeypatch.setattr(channels.smtplib, "SMTP", cls)
    send(_email("starttls"), msg(html="<b>hi</b>"))
    cls.assert_called_once_with("smtp.test", 25, timeout=20)
    smtp.starttls.assert_called_once()
    ctx = smtp.starttls.call_args.kwargs["context"]
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    smtp.login.assert_called_once_with("u", "pw-secret-1")
    m = smtp.send_message.call_args.args[0]
    assert m["Subject"] == "Title" and m["From"] == "a@x.com"
    assert m["To"] == "b@x.com, c@x.com"
    assert m.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in m.iter_parts()] == ["text/plain", "text/html"]


def test_email_ssl_and_none(monkeypatch):
    ssl_cls, plain_cls = MagicMock(), MagicMock()
    monkeypatch.setattr(channels.smtplib, "SMTP_SSL", ssl_cls)
    monkeypatch.setattr(channels.smtplib, "SMTP", plain_cls)
    send(_email("ssl"), msg())
    ssl_cls.assert_called_once()
    ctx = ssl_cls.call_args.kwargs["context"]
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    conn = _email("none").model_copy(update={"username": None})
    send(conn, msg())
    s = plain_cls.return_value.__enter__.return_value
    s.starttls.assert_not_called()
    s.login.assert_not_called()


def test_apprise_called(monkeypatch):
    inst = MagicMock()
    inst.notify.return_value = True
    monkeypatch.setattr(channels.apprise, "Apprise", lambda: inst)
    send(AppriseConn(id="a", name="a", type="apprise", urls=["json://a", "json://b"]), msg())
    assert [c.args[0] for c in inst.add.call_args_list] == ["json://a", "json://b"]
    inst.notify.assert_called_once_with(title="Title", body="Body text")


def test_apprise_false_is_error(monkeypatch):
    inst = MagicMock()
    inst.notify.return_value = False
    monkeypatch.setattr(channels.apprise, "Apprise", lambda: inst)
    with pytest.raises(NotifyError):
        send(AppriseConn(id="a", name="a", type="apprise", urls=["json://a"]), msg())


@respx.mock
def test_error_message_redacts_webhook_token():
    respx.post(HOOK).mock(side_effect=httpx.ConnectError(f"boom connecting to {HOOK}"))
    with pytest.raises(NotifyError) as ei:
        send(DiscordConn(id="d", name="d", type="discord", webhook_url=HOOK), msg())
    assert "abcDEF-secret_token" not in str(ei.value)
    assert ei.value.__cause__ is None


@respx.mock
def test_own_secret_scrubbed_from_webhook_url():
    url = "https://example.com/h/Zk93jfKKd8s"
    respx.post(url).respond(500)
    with pytest.raises(NotifyError) as ei:
        send(WebhookConn(id="w", name="w", type="webhook", url=url), msg())
    assert "Zk93jfKKd8s" not in str(ei.value)


@respx.mock
def test_retry_then_log_failure_never_raises(db):
    respx.post(HOOK).respond(500)
    sleeps = []
    conn = DiscordConn(id="d", name="d", type="discord", webhook_url=HOOK)
    ok = send_with_retry(db, conn, msg(), "new_findings", sleep=sleeps.append)
    assert ok is False
    assert sleeps == [1, 4]
    rows = db.scalars(select(NotificationLog)).all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.connection_id, r.event, r.success) == ("d", "new_findings", False)
    assert r.error and "abcDEF-secret_token" not in r.error


@respx.mock
def test_retry_succeeds_second_attempt(db):
    respx.post(HOOK).mock(side_effect=[httpx.Response(500), httpx.Response(204)])
    conn = DiscordConn(id="d", name="d", type="discord", webhook_url=HOOK)
    assert send_with_retry(db, conn, msg(), "x", sleep=lambda s: None) is True
    rows = db.scalars(select(NotificationLog)).all()
    assert len(rows) == 1 and rows[0].success and rows[0].error is None


def test_smtp_password_scrubbed_from_error(monkeypatch):
    cls = MagicMock(side_effect=OSError("auth failed for pw-secret-1"))
    monkeypatch.setattr(channels.smtplib, "SMTP", cls)
    with pytest.raises(NotifyError) as ei:
        send(_email("starttls"), msg())
    assert "pw-secret-1" not in str(ei.value)


@respx.mock
def test_retry_degenerate_args_never_raise(db):
    conn = DiscordConn(id="d", name="d", type="discord", webhook_url=HOOK)
    respx.post(HOOK).respond(500)
    assert send_with_retry(db, conn, msg(), "x", attempts=0, backoff=()) is False
    assert send_with_retry(db, conn, msg(), "x", attempts=2, backoff=()) is False
    assert len(db.scalars(select(NotificationLog)).all()) == 2


@respx.mock
def test_own_header_values_scrubbed_from_webhook_error():
    url = "https://example.com/h/plain"
    respx.post(url).mock(side_effect=httpx.ConnectError("refused; sent Bearer hdrval-98765 x"))
    conn = WebhookConn(id="w", name="w", type="webhook", url=url,
                       headers={"X-Thing": "hdrval-98765"})
    with pytest.raises(NotifyError) as ei:
        send(conn, msg())
    assert "hdrval-98765" not in str(ei.value)
