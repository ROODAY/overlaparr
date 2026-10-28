"""SPA serving: the built frontend answers unmatched non-API paths, never shadowing routes."""

import pytest
from fastapi.testclient import TestClient

from overlaparr import main

INDEX = "<html><div id=\"root\"></div></html>"


@pytest.fixture
def static(tmp_path, monkeypatch):
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "STATIC_DIR", root)
    return root


def _is_json_404(r):
    return r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_route_added_after_create_app_still_works(make_app, static):
    app = make_app()

    @app.get("/api/v1/late-route")
    def late():
        return {"late": True}

    @app.get("/late-page")
    def late_page():
        return {"page": True}

    with TestClient(app) as c:
        assert c.get("/api/v1/late-route").json() == {"late": True}
        assert c.get("/late-page").json() == {"page": True}


def test_unknown_api_path_is_json_404(make_app, static):
    with TestClient(make_app()) as c:
        for path in ("/api/v1/nope", "/api", "/api/", "/api/other"):
            r = c.get(path)
            assert _is_json_404(r), path
            assert INDEX not in r.text


def test_client_route_serves_index(make_app, static):
    with TestClient(make_app()) as c:
        for path in ("/", "/findings/123", "/some/route"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.headers["content-type"].startswith("text/html")
            assert r.text == INDEX


def test_missing_asset_and_non_get_stay_json_404(make_app, static):
    with TestClient(make_app()) as c:
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert _is_json_404(c.get("/assets/missing.js"))
        assert _is_json_404(c.post("/some/route"))


def test_static_absent_is_json_404(make_app, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path / "nothing")
    with TestClient(make_app()) as c:
        for path in ("/", "/findings/123", "/api/v1/nope"):
            assert _is_json_404(c.get(path)), path
        assert c.get("/health").json() == {"status": "ok"}
