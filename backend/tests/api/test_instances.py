import httpx
import respx

from overlaparr.config.store import MASK

KEY = "radarr-secret-key-1"
NEW = {"name": "Radarr", "type": "radarr", "url": "http://radarr:7878", "api_key": KEY}


def _create(c, **over):
    r = c.post("/api/v1/instances", json={**NEW, **over})
    assert r.status_code == 200, r.text
    return r.json()


def test_instance_crud_masks_key(authed, tmp_path):
    inst = _create(authed)
    assert inst["api_key"] == MASK and inst["id"] and inst["name"] == "Radarr"
    listed = authed.get("/api/v1/instances").json()
    assert [i["api_key"] for i in listed] == [MASK]
    assert KEY not in authed.get("/api/v1/instances").text
    assert KEY not in authed.get("/api/v1/settings").text
    assert KEY not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert KEY in (tmp_path / "secrets.yaml").read_text(encoding="utf-8")
    assert authed.get("/api/v1/setup/status").json()["instances_count"] == 1

    # Validation errors are 422 and never echo the submitted key.
    dup = authed.post("/api/v1/instances", json=NEW)
    assert dup.status_code == 422 and KEY not in dup.text
    bad = authed.post("/api/v1/instances", json={**NEW, "name": "x", "url": "not a url"})
    assert bad.status_code == 422 and KEY not in bad.text
    assert authed.post("/api/v1/instances", json={**NEW, "name": "y", "api_key": MASK}
                       ).status_code == 422

    assert authed.put("/api/v1/instances/nope", json=NEW).status_code == 404
    assert authed.delete(f"/api/v1/instances/{inst['id']}").status_code == 200
    assert authed.get("/api/v1/instances").json() == []
    assert authed.delete(f"/api/v1/instances/{inst['id']}").status_code == 404


def test_mask_roundtrip_keeps_secret(authed):
    store = authed.app.state.store
    inst = _create(authed)
    r = authed.put(f"/api/v1/instances/{inst['id']}",
                   json={**inst, "name": "Radarr 4K", "api_key": MASK})
    assert r.status_code == 200
    assert r.json()["name"] == "Radarr 4K" and r.json()["api_key"] == MASK
    cur = store.current.instances[0]
    assert (cur.name, cur.api_key) == ("Radarr 4K", KEY)

    authed.put(f"/api/v1/instances/{inst['id']}", json={**inst, "api_key": "new-key-999"})
    assert store.current.instances[0].api_key == "new-key-999"


@respx.mock
def test_instance_test_endpoint(authed):
    respx.get("http://radarr:7878/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"version": "5.1.0"}))
    body = {"type": "radarr", "url": "http://radarr:7878", "api_key": KEY}
    assert authed.post("/api/v1/instances/test", json=body).json() == {"ok": True,
                                                                       "version": "5.1.0"}
    assert authed.get("/api/v1/instances").json() == []  # nothing persisted

    respx.get("http://bad:7878/api/v3/system/status").mock(
        side_effect=httpx.ConnectError(f"refused apikey={KEY}"))
    r = authed.post("/api/v1/instances/test", json={**body, "url": "http://bad:7878"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["error"]
    assert KEY not in r.text

    # A masked key tests against the stored key of an existing instance.
    inst = _create(authed)
    route = respx.get("http://radarr:7878/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"version": "5.2.0"}))
    r = authed.post("/api/v1/instances/test", json={**body, "api_key": MASK, "id": inst["id"]})
    assert r.json() == {"ok": True, "version": "5.2.0"}
    assert route.calls.last.request.headers["X-Api-Key"] == KEY


@respx.mock
def test_profiles_and_tags(authed):
    inst = _create(authed)
    respx.get("http://radarr:7878/api/v3/qualityprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "HD", "cutoff": 3}]))
    respx.get("http://radarr:7878/api/v3/tag").mock(
        return_value=httpx.Response(200, json=[{"id": 4, "label": "dupe-elsewhere"}]))
    assert authed.get(f"/api/v1/instances/{inst['id']}/profiles").json() == [
        {"id": 1, "name": "HD"}]
    assert authed.get(f"/api/v1/instances/{inst['id']}/tags").json() == [
        {"id": 4, "label": "dupe-elsewhere"}]
    assert authed.get("/api/v1/instances/nope/tags").status_code == 404

    respx.get("http://radarr:7878/api/v3/tag").mock(return_value=httpx.Response(500))
    r = authed.get(f"/api/v1/instances/{inst['id']}/tags")
    assert r.status_code == 502 and KEY not in r.text


def test_delete_instance_removes_secret(authed, tmp_path):
    inst = _create(authed)
    key_name = f"instance_{inst['id']}_api_key"
    assert authed.app.state.store.get_secret(key_name) == KEY
    authed.delete(f"/api/v1/instances/{inst['id']}")
    assert authed.app.state.store.get_secret(key_name) is None
    assert KEY not in (tmp_path / "secrets.yaml").read_text(encoding="utf-8")


@respx.mock
def test_masked_key_not_reused_for_a_new_url(authed):
    inst = _create(authed)
    evil = respx.get("http://evil:7878/api/v3/system/status").respond(json={"version": "5"})
    r = authed.post("/api/v1/instances/test", json={
        "type": "radarr", "url": "http://evil:7878", "api_key": MASK, "id": inst["id"]})
    assert r.status_code == 422
    assert r.json()["detail"] == "Re-enter the API key for the new URL"
    assert not evil.called

    r = authed.put(f"/api/v1/instances/{inst['id']}",
                   json={**inst, "url": "http://evil:7878", "api_key": MASK})
    assert r.status_code == 422
    assert r.json()["detail"] == "Re-enter the API key for the new URL"
    stored = authed.app.state.store.current.instances[0]
    assert stored.url == "http://radarr:7878"

    # Same URL keeps the key; a new URL with the key typed again is fine.
    ok = respx.get("http://radarr:7878/api/v3/system/status").respond(json={"version": "5"})
    r = authed.post("/api/v1/instances/test", json={
        "type": "radarr", "url": "http://radarr:7878", "api_key": MASK, "id": inst["id"]})
    assert r.json()["ok"] is True and ok.called
    r = authed.put(f"/api/v1/instances/{inst['id']}",
                   json={**inst, "url": "http://radarr2:7878", "api_key": KEY})
    assert r.status_code == 200, r.text
