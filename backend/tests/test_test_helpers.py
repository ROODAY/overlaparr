"""The shared test helpers must see every request production code attempts."""

import asyncio

import httpx
import pytest
import respx

from tests.actions.helpers import non_get_calls


@respx.mock
def test_unmocked_write_is_recorded():
    respx.get("http://sonarr/api/v3/series").respond(json=[])
    httpx.get("http://sonarr/api/v3/series")
    with pytest.raises(AssertionError):  # production code would swallow this
        httpx.put("http://sonarr/api/v3/series/editor", json={})
    assert [c.request.method for c in non_get_calls()] == ["PUT"]


@respx.mock
def test_unmocked_async_write_is_recorded():
    async def attempt():
        async with httpx.AsyncClient() as client:
            with pytest.raises(AssertionError):
                await client.delete("http://radarr/api/v3/movie/1")

    asyncio.run(attempt())
    assert [c.request.method for c in non_get_calls()] == ["DELETE"]
