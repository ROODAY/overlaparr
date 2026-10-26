"""Thin httpx wrapper for the Sonarr/Radarr v3 API."""

from __future__ import annotations

from typing import Any

import httpx

from overlaparr.logging_setup import redact


class ArrError(Exception):
    def __init__(self, status: int | None, message: str):
        self.status = status
        self.message = redact(message)
        super().__init__(self.message)


class ArrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30):
        self._api_key = api_key
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )

    def _scrub(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "***")
        return redact(text)

    def get(self, path: str, **params: Any) -> Any:
        try:
            resp = self._http.get(path, params=params or None)
        except httpx.HTTPError as e:
            raise ArrError(None, self._scrub(f"{type(e).__name__}: {e}")) from None
        if resp.status_code >= 400:
            raise ArrError(
                resp.status_code, self._scrub(f"{path} returned HTTP {resp.status_code}")
            )
        try:
            return resp.json()
        except ValueError:
            raise ArrError(resp.status_code, f"{path} returned invalid JSON") from None

    def close(self) -> None:
        self._http.close()
