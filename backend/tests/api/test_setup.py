from fastapi.testclient import TestClient

from overlaparr.auth import SESSION_COOKIE
from tests.api.conftest import USER


def test_health_open(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_setup_flow(client):
    r = client.get("/api/v1/setup/status")
    assert r.json() == {
        "needs_user": True, "plex_connected": False, "home_server_set": False,
        "instances_count": 0,
    }
    bad = client.post("/api/v1/setup/user", json={"username": "alice", "password": "short"})
    assert bad.status_code == 422
    assert "at least 8" in bad.json()["detail"]

    r = client.post("/api/v1/setup/user", json=USER)
    assert r.status_code == 200
    assert SESSION_COOKIE in r.cookies
    # Logged in straight away.
    assert client.get("/api/v1/auth/me").json() == {"principal": "alice"}
    assert client.get("/api/v1/setup/status").json()["needs_user"] is False
    # Setup is closed once a user exists.
    again = client.post("/api/v1/setup/user", json={"username": "bob", "password": "x" * 10})
    assert again.status_code == 403

    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").status_code == 401

    wrong = client.post("/api/v1/auth/login", json={**USER, "password": "nope-nope"})
    unknown = client.post("/api/v1/auth/login", json={**USER, "username": "mallory"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password"}

    ok = client.post("/api/v1/auth/login", json=USER)
    assert ok.status_code == 200
    assert client.get("/api/v1/auth/me").json() == {"principal": "alice"}


def test_routes_require_auth(client):
    # Before setup every protected route says setup is required.
    assert client.get("/api/v1/settings").status_code == 401
    client.post("/api/v1/setup/user", json=USER)
    client.cookies.clear()
    for method, path in [
        ("get", "/api/v1/settings"), ("get", "/api/v1/servers"), ("get", "/api/v1/instances"),
        ("post", "/api/v1/plex/pin"), ("put", "/api/v1/servers/home"),
        ("get", "/api/v1/auth/me"), ("post", "/api/v1/auth/logout"),
        ("get", "/api/v1/settings/config.yaml"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path
    # Open routes stay open.
    assert client.get("/api/v1/setup/status").status_code == 200
    assert client.get("/health").status_code == 200


def test_api_key_auth(client):
    client.post("/api/v1/setup/user", json=USER)
    client.cookies.clear()
    key = client.app.state.store.get_secret("app_api_key")
    r = client.get("/api/v1/auth/me", headers={"X-Api-Key": key})
    assert r.json() == {"principal": "api-key"}


def test_spa_served_with_history_fallback(make_app, tmp_path, monkeypatch):
    from overlaparr import main

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.ico").write_text("ico", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", static)
    with TestClient(make_app()) as c:
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/favicon.ico").text == "ico"
        assert c.get("/findings/12").text == "<html>spa</html>"
        assert c.get("/").text == "<html>spa</html>"
        assert c.get("/api/v1/nope").status_code == 404
        assert c.get("/api/v1/nope").headers["content-type"].startswith("application/json")
        assert c.get("/..%2Fsecrets.yaml").text == "<html>spa</html>"
        assert c.get("/health").json() == {"status": "ok"}


def test_spa_missing_is_quiet(make_app, tmp_path, monkeypatch):
    from overlaparr import main

    monkeypatch.setattr(main, "STATIC_DIR", tmp_path / "nothing")
    with TestClient(make_app()) as c:
        assert c.get("/findings").status_code == 404
        assert c.get("/health").json() == {"status": "ok"}


def test_background_services_follow_lifespan(make_app):
    app = make_app(start_background=True)
    with TestClient(app):
        assert app.state.scheduler.scheduler.running
        assert {j.id for j in app.state.scheduler.scheduler.get_jobs()} >= {"scan", "retention"}
    assert not app.state.scheduler.scheduler.running


def test_post_hooks_registered_in_order(make_app):
    app = make_app()
    names = [h.__name__ for h in app.state.scan_engine._hooks]
    assert names == ["sync_tags", "handle_losses", "run_rules", "dispatch_after_scan"]
