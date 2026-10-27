import httpx
import respx
from sqlalchemy import select

from overlaparr.db.models import AppState
from overlaparr.plex import auth as plex_auth

PINS = "https://plex.tv/api/v2/pins"


@respx.mock
def test_start_and_check_pin():
    route = respx.post(PINS).mock(return_value=httpx.Response(201, json={"id": 7, "code": "abcd"}))
    pin = plex_auth.start_pin("cid-1")
    assert pin == {
        "id": 7, "code": "abcd",
        "auth_url": "https://app.plex.tv/auth#?clientID=cid-1&code=abcd"
                    "&context%5Bdevice%5D%5Bproduct%5D=Overlaparr",
    }
    req = route.calls.last.request
    assert req.url.params["strong"] == "true"
    assert req.headers["X-Plex-Product"] == "Overlaparr"
    assert req.headers["X-Plex-Client-Identifier"] == "cid-1"
    assert req.headers["Accept"] == "application/json"

    respx.get(f"{PINS}/7").mock(return_value=httpx.Response(200, json={"id": 7, "authToken": None}))
    assert plex_auth.check_pin(7, "cid-1") is None
    respx.get(f"{PINS}/7").mock(return_value=httpx.Response(200, json={"id": 7, "authToken": "tok"}))
    assert plex_auth.check_pin(7, "cid-1") == "tok"


@respx.mock
def test_pin_flow_stores_secret(authed, tmp_path):
    token = "plex-account-token-xyz"
    respx.post(PINS).mock(return_value=httpx.Response(201, json={"id": 42, "code": "wxyz"}))
    r = authed.post("/api/v1/plex/pin")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 42 and body["code"] == "wxyz"
    with authed.app.state.db_factory() as db:
        client_id = db.scalar(
            select(AppState.value).where(AppState.key == "plex_client_identifier")
        )
    assert client_id and f"clientID={client_id}" in body["auth_url"]

    respx.get(f"{PINS}/42").mock(return_value=httpx.Response(200, json={"authToken": None}))
    assert authed.get("/api/v1/plex/pin/42").json() == {"connected": False}

    respx.get(f"{PINS}/42").mock(return_value=httpx.Response(200, json={"authToken": token}))
    r = authed.get("/api/v1/plex/pin/42")
    assert r.json() == {"connected": True}
    assert token not in r.text

    config = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "token: !secret plex_token" in config
    assert token not in config
    assert token in (tmp_path / "secrets.yaml").read_text(encoding="utf-8")
    assert authed.app.state.store.current.plex.token == token
    assert authed.get("/api/v1/setup/status").json()["plex_connected"] is True
    assert token not in authed.get("/api/v1/settings").text
    # The client id is reused on the next PIN.
    respx.post(PINS).mock(return_value=httpx.Response(201, json={"id": 43, "code": "q"}))
    assert f"clientID={client_id}" in authed.post("/api/v1/plex/pin").json()["auth_url"]


@respx.mock
def test_expired_pin_and_plex_down(authed):
    respx.get(f"{PINS}/9").mock(return_value=httpx.Response(404))
    assert authed.get("/api/v1/plex/pin/9").status_code == 404
    respx.post(PINS).mock(side_effect=httpx.ConnectError("boom"))
    assert authed.post("/api/v1/plex/pin").status_code == 502


def test_paste_token_and_sign_out(authed, tmp_path):
    token = "pasted-token-12345"
    r = authed.put("/api/v1/plex/token", json={"token": token})
    assert r.status_code == 200 and token not in r.text
    assert "token: !secret plex_token" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert authed.app.state.store.current.plex.token == token

    assert authed.delete("/api/v1/plex/token").status_code == 200
    assert authed.app.state.store.current.plex.token is None
    assert token not in (tmp_path / "secrets.yaml").read_text(encoding="utf-8")
    assert authed.get("/api/v1/setup/status").json()["plex_connected"] is False
    assert authed.put("/api/v1/plex/token", json={"token": "  "}).status_code == 422
