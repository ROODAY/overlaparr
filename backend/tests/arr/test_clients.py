import json
from pathlib import Path

import httpx
import pytest
import respx

from overlaparr.arr.client import ArrClient, ArrError
from overlaparr.arr.snapshot import make_client
from overlaparr.config.schema import InstanceConfig

FIX = Path(__file__).parent.parent / "fixtures"
KEY = "supersecretapikey123"


def load(rel: str):
    return json.loads((FIX / rel).read_text(encoding="utf-8"))


@respx.mock
def test_api_key_header_sent():
    route = respx.get("http://radarr:7878/api/v3/system/status").respond(
        json=load("radarr/system_status.json")
    )
    client = make_client(
        InstanceConfig(id="r1", name="R", type="radarr", url="http://radarr:7878/", api_key=KEY)
    )
    assert client.status()["appName"] == "Radarr"
    req = route.calls.last.request
    assert req.headers["X-Api-Key"] == KEY
    assert KEY not in str(req.url)


@respx.mock
def test_error_hides_api_key():
    respx.get("http://x/api/v3/tag").respond(401)
    with pytest.raises(ArrError) as ei:
        ArrClient("http://x", KEY).get("/api/v3/tag")
    assert ei.value.status == 401
    assert KEY not in str(ei.value)


@respx.mock
def test_transport_error_hides_api_key():
    respx.get("http://x/api/v3/tag").mock(
        side_effect=httpx.ConnectError(f"boom apikey={KEY} failed")
    )
    with pytest.raises(ArrError) as ei:
        ArrClient("http://x", KEY).get("/api/v3/tag")
    assert ei.value.status is None
    assert KEY not in str(ei.value)


def test_make_client_types_and_url():
    r = make_client(InstanceConfig(id="a", name="a", type="radarr", url="http://h:1/", api_key=KEY))
    s = make_client(InstanceConfig(id="b", name="b", type="sonarr", url="http://h:2", api_key=KEY))
    assert type(r).__name__ == "RadarrClient" and type(s).__name__ == "SonarrClient"
    assert str(r._http.base_url).rstrip("/") == "http://h:1"
    assert r._http.timeout.read == 30
