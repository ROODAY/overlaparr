from datetime import UTC, datetime, timedelta

import yaml
from sqlalchemy import select

from overlaparr.api import notifications
from overlaparr.config.store import MASK
from overlaparr.db.models import NotificationLog
from overlaparr.notify.channels import NotifyError

NOTIFY = "/api/v1/notifications"
HOOK = "https://discord.com/api/webhooks/123/supersecrettoken"


def _create(authed, **body):
    r = authed.post(NOTIFY, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _discord(authed, **kw):
    return _create(authed, **{"type": "discord", "name": "D", "webhook_url": HOOK, **kw})


def _secrets(tmp_path):
    return yaml.safe_load((tmp_path / "secrets.yaml").read_text(encoding="utf-8"))


def test_notification_secret_masked(authed, tmp_path):
    conn = _discord(authed, id="mine")
    assert conn["id"] != "mine"
    assert conn["webhook_url"] == MASK
    assert _secrets(tmp_path)[f"notify_{conn['id']}_webhook_url"] == HOOK

    app = _create(authed, type="apprise", name="A", urls=["pover://user@token1", "tgram://bot/x"])
    assert app["urls"] == [MASK, MASK]
    email = _create(authed, type="email", name="E", host="smtp", from_addr="a@b",
                    to=["c@d"], username="u", password="pw-secret-1")
    assert email["password"] == MASK

    listed = authed.get(NOTIFY).json()
    assert [c["id"] for c in listed] == [conn["id"], app["id"], email["id"]]
    assert [c["last_sent"] for c in listed] == [None, None, None]
    text = authed.get(NOTIFY).text
    for secret in (HOOK, "pover://user@token1", "tgram://bot/x", "pw-secret-1"):
        assert secret not in text

    # GET output sent back unchanged keeps every secret.
    for c in listed:
        r = authed.put(f"{NOTIFY}/{c['id']}", json={**c, "enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True
    store = authed.app.state.store
    stored = {c.id: c for c in store.current.notifications.connections}
    assert stored[conn["id"]].webhook_url == HOOK
    assert stored[app["id"]].urls == ["pover://user@token1", "tgram://bot/x"]
    assert stored[email["id"]].password == "pw-secret-1"

    # PATCH merges and keeps masked secrets; a new value replaces the secret.
    r = authed.patch(f"{NOTIFY}/{conn['id']}", json={"name": "Renamed", "webhook_url": MASK})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
    r = authed.patch(f"{NOTIFY}/{app['id']}", json={"urls": ["json://new"]})
    assert r.json()["urls"] == [MASK]
    stored = {c.id: c for c in store.current.notifications.connections}
    assert stored[conn["id"]].webhook_url == HOOK
    assert stored[app["id"]].urls == ["json://new"]

    # A partially masked apprise list is ambiguous; a mask on create has nothing to keep.
    r = authed.patch(f"{NOTIFY}/{app['id']}", json={"urls": [MASK, "json://x"]})
    assert r.status_code == 422
    r = authed.post(NOTIFY, json={"type": "discord", "name": "x", "webhook_url": MASK})
    assert r.status_code == 422

    # Validation, unknown ids.
    assert authed.post(NOTIFY, json={"type": "discord", "name": "x"}).status_code == 422
    assert authed.put(f"{NOTIFY}/nope", json={"type": "discord", "name": "x",
                                               "webhook_url": HOOK}).status_code == 404
    assert authed.patch(f"{NOTIFY}/nope", json={"name": "x"}).status_code == 404
    assert authed.delete(f"{NOTIFY}/nope").status_code == 404

    # Changing type needs the new type's required fields.
    r = authed.patch(f"{NOTIFY}/{conn['id']}", json={"type": "webhook"})
    assert r.status_code == 422
    assert HOOK not in r.text
    r = authed.patch(f"{NOTIFY}/{conn['id']}",
                     json={"type": "webhook", "url": "https://hook.example/x"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == MASK
    secrets = _secrets(tmp_path)
    assert f"notify_{conn['id']}_webhook_url" not in secrets  # orphan removed
    assert secrets[f"notify_{conn['id']}_url"] == "https://hook.example/x"

    # Delete removes the connection and its secrets.
    r = authed.delete(f"{NOTIFY}/{conn['id']}")
    assert r.status_code == 204
    assert conn["id"] not in [c["id"] for c in authed.get(NOTIFY).json()]
    assert not any(k.startswith(f"notify_{conn['id']}_") for k in _secrets(tmp_path))


def test_notification_test_endpoint(authed, monkeypatch):
    conn = _discord(authed)  # enabled defaults to False: test sends anyway
    calls = []

    def fake_send(c, msg):
        calls.append((c, msg))

    monkeypatch.setattr(notifications, "send", fake_send)
    r = authed.post(f"{NOTIFY}/{conn['id']}/test")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    sent, msg = calls[0]
    assert sent.webhook_url == HOOK  # the stored, unmasked connection
    assert msg.title == "Overlaparr test notification"
    assert msg.text == "If you can see this, notifications are working."

    def failing_send(c, msg):
        calls.append((c, msg))
        raise NotifyError(f"404 for url {c.webhook_url} password=hunter2xyz")

    monkeypatch.setattr(notifications, "send", failing_send)
    r = authed.post(f"{NOTIFY}/{conn['id']}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "supersecrettoken" not in body["error"] and "hunter2xyz" not in body["error"]
    assert len(calls) == 2  # no retries

    assert authed.post(f"{NOTIFY}/nope/test").status_code == 404

    with authed.app.state.db_factory() as db:
        rows = db.scalars(select(NotificationLog).order_by(NotificationLog.id)).all()
    assert [(r.connection_id, r.event, r.success) for r in rows] == [
        (conn["id"], "test", True), (conn["id"], "test", False)]
    assert "supersecrettoken" not in rows[1].error

    listed = authed.get(NOTIFY).json()
    assert listed[0]["last_sent"] is not None

    history = authed.get(f"{NOTIFY}/history").json()
    assert [(h["connection_id"], h["connection_name"], h["event"], h["success"])
            for h in history] == [(conn["id"], "D", "test", False), (conn["id"], "D", "test", True)]
    assert history[1]["error"] is None


def test_history_order_limit_and_last_sent(authed):
    conn = _discord(authed)
    now = datetime.now(UTC)
    with authed.app.state.db_factory() as db:
        db.add_all([
            NotificationLog(ts=now - timedelta(hours=3), connection_id=conn["id"],
                            event="scan", success=True, error=None),
            NotificationLog(ts=now - timedelta(hours=1), connection_id=conn["id"],
                            event="scan", success=False, error="boom"),
            NotificationLog(ts=now - timedelta(hours=2), connection_id="gone",
                            event="digest", success=True, error=None),
        ])
        db.commit()
    history = authed.get(f"{NOTIFY}/history").json()
    assert [(h["connection_id"], h["connection_name"]) for h in history] == [
        (conn["id"], "D"), ("gone", None), (conn["id"], "D")]
    assert set(history[0]) == {"ts", "connection_id", "connection_name", "event", "success",
                               "error"}
    assert len(authed.get(f"{NOTIFY}/history?limit=1").json()) == 1
    assert authed.get(f"{NOTIFY}/history?limit=501").status_code == 422

    listed = authed.get(NOTIFY).json()
    assert listed[0]["last_sent"].startswith((now - timedelta(hours=3)).isoformat()[:16])


WEBHOOK = "https://hooks.example/in/abc123"
HDRS = {"Authorization": "Bearer hdr-secret-123", "X-Team": "team-val-456"}


def _webhook(authed, **kw):
    return _create(authed, **{"type": "webhook", "name": "W", "url": WEBHOOK,
                              "headers": dict(HDRS), **kw})


def _stored(authed, conn_id):
    return next(c for c in authed.app.state.store.current.notifications.connections
                if c.id == conn_id)


def test_webhook_header_values_live_in_secrets_yaml(authed, tmp_path):
    conn = _webhook(authed)
    assert conn["headers"] == {"Authorization": MASK, "X-Team": MASK}
    secrets = _secrets(tmp_path)
    assert secrets[f"notify_{conn['id']}_header_Authorization"] == HDRS["Authorization"]
    assert secrets[f"notify_{conn['id']}_header_X-Team"] == HDRS["X-Team"]
    config_text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    download = authed.get("/api/v1/settings/config.yaml").text
    for text in (config_text, download):
        assert "Authorization" in text  # header names stay visible
        for value in HDRS.values():
            assert value not in text
    assert _stored(authed, conn["id"]).headers == HDRS


def test_webhook_header_values_masked_by_every_api(authed):
    conn = _webhook(authed)
    for text in (authed.get(NOTIFY).text, authed.get("/api/v1/settings").text):
        for value in HDRS.values():
            assert value not in text
    [listed] = authed.get(NOTIFY).json()
    assert listed["headers"] == {"Authorization": MASK, "X-Team": MASK}
    settings = authed.get("/api/v1/settings").json()
    [conn_out] = settings["notifications"]["connections"]
    assert conn_out["headers"] == {"Authorization": MASK, "X-Team": MASK}
    assert conn_out["id"] == conn["id"]


def test_webhook_header_values_registered_for_log_redaction(authed):
    from overlaparr.logging_setup import redact

    _webhook(authed)
    for value in HDRS.values():
        assert value not in redact(f"sent header {value} somewhere")


def test_masked_header_keeps_stored_value_per_name(authed, tmp_path):
    conn = _webhook(authed)
    r = authed.put(f"{NOTIFY}/{conn['id']}", json=conn)  # GET output sent back unchanged
    assert r.status_code == 200, r.text
    assert _stored(authed, conn["id"]).headers == HDRS

    r = authed.patch(f"{NOTIFY}/{conn['id']}",
                     json={"headers": {"Authorization": MASK, "X-New": "brand-new-789"}})
    assert r.status_code == 200, r.text
    assert r.json()["headers"] == {"Authorization": MASK, "X-New": MASK}
    assert _stored(authed, conn["id"]).headers == {"Authorization": HDRS["Authorization"],
                                                   "X-New": "brand-new-789"}
    secrets = _secrets(tmp_path)
    assert f"notify_{conn['id']}_header_X-Team" not in secrets  # dropped header's secret
    assert secrets[f"notify_{conn['id']}_header_X-New"] == "brand-new-789"

    # A mask for a header that has no stored value has nothing to keep.
    r = authed.patch(f"{NOTIFY}/{conn['id']}", json={"headers": {"X-Other": MASK}})
    assert r.status_code == 422

    assert authed.delete(f"{NOTIFY}/{conn['id']}").status_code == 204
    assert not [k for k in _secrets(tmp_path) if k.startswith(f"notify_{conn['id']}_")]


def test_masked_headers_not_reused_for_a_new_url(authed):
    conn = _webhook(authed)
    r = authed.put(f"{NOTIFY}/{conn['id']}", json={**conn, "url": "https://evil.example/x"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Re-enter the headers for the new URL"
    r = authed.patch(f"{NOTIFY}/{conn['id']}", json={"url": "https://evil.example/x"})
    assert r.status_code == 422  # PATCH merges the stored headers: still a reuse
    assert _stored(authed, conn["id"]).url == WEBHOOK
    # New URL with the header values typed in again is fine.
    r = authed.put(f"{NOTIFY}/{conn['id']}",
                   json={**conn, "url": "https://new.example/x", "headers": HDRS})
    assert r.status_code == 200, r.text
    assert _stored(authed, conn["id"]).url == "https://new.example/x"


def test_masked_email_password_not_reused_for_a_new_host(authed):
    email = _create(authed, type="email", name="E", host="smtp.good", from_addr="a@b",
                    to=["c@d"], username="u", password="pw-secret-1")
    r = authed.put(f"{NOTIFY}/{email['id']}", json={**email, "host": "smtp.evil"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Re-enter the password for the new host"
    r = authed.patch(f"{NOTIFY}/{email['id']}", json={"host": "smtp.evil"})
    assert r.status_code == 422
    assert _stored(authed, email["id"]).host == "smtp.good"
    r = authed.put(f"{NOTIFY}/{email['id']}", json={**email, "port": 2525})
    assert r.status_code == 200, r.text
    assert _stored(authed, email["id"]).password == "pw-secret-1"
    r = authed.put(f"{NOTIFY}/{email['id']}",
                   json={**email, "host": "smtp.new", "password": "pw-secret-2"})
    assert r.status_code == 200, r.text
