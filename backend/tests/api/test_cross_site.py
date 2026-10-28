"""CSRF-style protection and clickjacking/CSP headers."""

import pytest

from tests.api.conftest import USER

UNSAFE = [("post", "/api/v1/instances"), ("put", "/api/v1/settings"),
          ("patch", "/api/v1/auth/me"), ("delete", "/api/v1/instances/x")]


def _req(authed, method, path, **headers):
    kw = {} if method == "delete" else {"json": {}}
    return getattr(authed, method)(path, headers=headers, **kw)


@pytest.mark.parametrize("method,path", UNSAFE)
def test_cross_site_fetch_metadata_is_rejected(authed, method, path):
    r = _req(authed, method, path, **{"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.json() == {"detail": "Cross-site request rejected"}


@pytest.mark.parametrize("method,path", UNSAFE)
def test_foreign_origin_is_rejected(authed, method, path):
    r = _req(authed, method, path, **{"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = _req(authed, method, path, **{"Origin": "http://localhost:9999"})
    assert r.status_code == 403  # same host name, different port is foreign


def test_same_origin_and_headerless_requests_pass(authed):
    host = "testserver"
    for origin in (f"http://{host}", f"https://{host}", f"http://{host}:80"):
        r = _req(authed, "put", "/api/v1/settings", **{"Origin": origin})
        assert r.status_code != 403, origin
    for site in ("same-origin", "same-site", "none"):
        assert _req(authed, "put", "/api/v1/settings",
                    **{"Sec-Fetch-Site": site}).status_code != 403
    # curl / scripts with an API key: neither header at all.
    key = authed.app.state.store.get_secret("app_api_key")
    r = authed.put("/api/v1/settings", headers={"X-Api-Key": key}, json={})
    assert r.status_code == 200
    # The Vite dev proxy forwards Origin: http://localhost:5173 with Host: localhost:8686.
    r = authed.put("/api/v1/settings",
                   headers={"Origin": "http://localhost:5173", "Host": "localhost:8686"},
                   json={})
    assert r.status_code == 403  # the proxy must rewrite Origin (changeOrigin)


def test_opaque_origin_is_rejected(authed):
    # A sandboxed iframe's "null" origin has no host to match: treat it as foreign.
    r = _req(authed, "put", "/api/v1/settings", **{"Origin": "null"})
    assert r.status_code == 403


def test_safe_methods_from_another_origin_still_work(authed):
    # Reading is not state-changing: no CSRF protection needed there.
    r = authed.get("/api/v1/settings", headers={"Sec-Fetch-Site": "cross-site",
                                                "Origin": "https://evil.example"})
    assert r.status_code == 200


def test_non_api_routes_are_not_blocked(client):
    assert client.get("/health").status_code == 200
    assert client.post("/health", headers={"Origin": "https://evil.example"}).status_code == 405


def test_security_headers_on_every_response(authed):
    for method, path, expected in [("get", "/health", 200), ("get", "/api/v1/settings", 200),
                                   ("get", "/api/v1/nope", 404), ("put", "/api/v1/settings",
                                                                   200)]:
        r = getattr(authed, method)(path, **({} if method == "get" else {"json": {}}))
        assert r.status_code == expected, path
        assert r.headers["X-Frame-Options"] == "DENY", path
        assert r.headers["Content-Security-Policy"] == "frame-ancestors 'none'", path


def test_security_headers_on_errors_and_spa(make_app, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from overlaparr import main

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", static)
    with TestClient(make_app()) as c:
        c.post("/api/v1/setup/user", json=USER)
        for r in (c.get("/findings/12"), c.get("/api/v1/nope"),
                  c.post("/api/v1/instances", json={})):
            assert r.headers["X-Frame-Options"] == "DENY"
            assert r.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
