import json

from fastapi import HTTPException
from fastapi.testclient import TestClient

from overlaparr.logging_setup import register_secret
from tests.api.conftest import USER


def test_get_settings_masked(authed):
    authed.put("/api/v1/plex/token", json={"token": "plex-token-abc"})
    body = authed.get("/api/v1/settings").json()
    assert body["config_error"] is None
    assert body["plex"]["token"] == "********"
    assert body["scan"]["cron"] == "0 */6 * * *"


def test_put_settings_partial(authed, tmp_path):
    r = authed.put("/api/v1/settings", json={
        "scan": {"cron": "0 3 * * *"}, "tags": {"enabled": True},
        "notifications": {"app_url": "http://overlaparr.local"},
        "auth": {"local_bypass": True}, "action_log": {"retention_days": 30},
        "logging": {"level": "DEBUG"},
    })
    assert r.status_code == 200, r.text
    cfg = authed.app.state.store.current
    assert cfg.scan.cron == "0 3 * * *"
    assert cfg.scan.page_size == 500  # untouched fields kept
    assert cfg.tags.enabled is True
    assert cfg.notifications.app_url == "http://overlaparr.local"
    assert cfg.auth.local_bypass is True
    assert cfg.action_log.retention_days == 30
    assert r.json()["scan"]["cron"] == "0 3 * * *"
    assert "0 3 * * *" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_put_settings_rejects_other_sections(authed):
    for body in [
        {"plex": {"home_server": "x"}}, {"instances": []}, {"rules": []},
        {"notifications": {"connections": []}}, {"scan": "nope"},
    ]:
        assert authed.put("/api/v1/settings", json=body).status_code == 422, body
    bad = authed.put("/api/v1/settings", json={"scan": {"cron": "not cron"}})
    assert bad.status_code == 422
    bad = authed.put("/api/v1/settings", json={"tags": {"dupe": "Bad Tag"}})
    assert bad.status_code == 422
    assert authed.app.state.store.current.tags.dupe == "dupe-elsewhere"
    clash = authed.put("/api/v1/settings", json={"tags": {"ignore": "dupe-elsewhere"}})
    assert clash.status_code == 422
    assert "must be different" in json.dumps(clash.json())
    assert authed.app.state.store.current.tags.ignore == "dupe-ignore"


def test_config_error_surfaced(authed, tmp_path):
    store = authed.app.state.store
    (tmp_path / "config.yaml").write_text('scan: {page_size: "lots"}\n', encoding="utf-8")
    store.reload_from_disk()  # what the watcher does on an external edit
    body = authed.get("/api/v1/settings").json()
    assert "page_size" in body["config_error"]
    assert body["scan"]["page_size"] == 500  # still the last good config
    r = authed.put("/api/v1/settings", json={"tags": {"enabled": True}})
    assert r.status_code == 409
    assert "page_size" in r.json()["detail"]
    r = authed.post("/api/v1/instances", json={
        "name": "R", "type": "radarr", "url": "http://r", "api_key": "k" * 10})
    assert r.status_code == 409
    assert 'page_size: "lots"' in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_config_yaml_download(authed, tmp_path):
    authed.put("/api/v1/plex/token", json={"token": "plex-token-abc"})
    r = authed.get("/api/v1/settings/config.yaml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/yaml")
    assert r.text == (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "plex-token-abc" not in r.text


def test_cron_preview(authed):
    r = authed.get("/api/v1/settings/cron-preview", params={"cron": "0 3 * * *"})
    assert r.status_code == 200
    nxt = r.json()["next"]
    assert len(nxt) == 5 and all("T03:00:00" in t for t in nxt)
    assert authed.get("/api/v1/settings/cron-preview",
                      params={"cron": "bogus"}).status_code == 422


def test_logging_reconfigured_on_change(authed):
    import logging

    authed.put("/api/v1/settings", json={"logging": {"level": "WARNING"}})
    assert logging.getLogger().level == logging.WARNING


def test_error_bodies_redacted(make_app):
    secret = "super-secret-value-123"
    app = make_app()

    @app.get("/api/v1/boom-test")
    def boom():
        raise RuntimeError(f"connection failed for {secret} with token=abcdef123")

    @app.get("/api/v1/http-test")
    def http_err():
        raise HTTPException(502, detail=f"upstream said apikey={secret}")

    register_secret(secret)
    with TestClient(app) as c:  # the 500 is a response, not a re-raise
        r = c.get("/api/v1/boom-test")
        assert r.status_code == 500
        assert secret not in r.text and "abcdef123" not in r.text
        assert "connection failed" in r.json()["detail"]
        r = c.get("/api/v1/http-test")
        assert r.status_code == 502 and secret not in r.text


def test_invalid_config_at_startup_keeps_running(make_app, tmp_path):
    broken = b'scan: {page_size: "lots"}\n'
    (tmp_path / "config.yaml").write_bytes(broken)
    app = make_app()
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}
        assert c.post("/api/v1/setup/user", json=USER).status_code == 200
        body = c.get("/api/v1/settings").json()
        assert "page_size" in body["config_error"]
        assert c.put("/api/v1/settings", json={"tags": {"enabled": True}}).status_code == 409
        assert (tmp_path / "config.yaml").read_bytes() == broken
        # A scan on the fallback config fails cleanly.
        app.state.scan_engine.run_sync("manual")
        (tmp_path / "config.yaml").write_text("scan:\n  page_size: 42\n", encoding="utf-8")
        assert app.state.store.reload_from_disk() is True
        body = c.get("/api/v1/settings").json()
        assert body["config_error"] is None and body["scan"]["page_size"] == 42
