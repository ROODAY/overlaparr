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


class DryRunViolation(Exception):
    """A write was attempted on a client that is in dry-run mode."""


class ArrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30, dry_run: bool = False):
        self._api_key = api_key
        self.dry_run = dry_run
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )

    def _scrub(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "***")
        return redact(text)

    def _guard(self, method: str, path: str) -> None:
        if self.dry_run:
            raise DryRunViolation(f"{method} {path} attempted on a dry-run client")

    def _request(
        self, method: str, path: str, params: dict | None = None, json: Any = None
    ) -> Any:
        try:
            resp = self._http.request(method, path, params=params or None, json=json)
        except httpx.HTTPError as e:
            raise ArrError(None, self._scrub(f"{type(e).__name__}: {e}")) from None
        if resp.status_code >= 400:
            raise ArrError(
                resp.status_code, self._scrub(f"{path} returned HTTP {resp.status_code}")
            )
        if not resp.content.strip():
            return None
        try:
            return resp.json()
        except ValueError:
            raise ArrError(resp.status_code, f"{path} returned invalid JSON") from None

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params)

    def post(self, path: str, json: Any = None, **params: Any) -> Any:
        self._guard("POST", path)
        return self._request("POST", path, params, json)

    def put(self, path: str, json: Any = None, **params: Any) -> Any:
        self._guard("PUT", path)
        return self._request("PUT", path, params, json)

    def delete(self, path: str, json: Any = None, **params: Any) -> Any:
        self._guard("DELETE", path)
        return self._request("DELETE", path, params, json)

    def ensure_tag(self, label: str) -> int:
        """Id of the tag with this label (case-insensitive); created if absent."""
        for tag in self.get("/api/v3/tag"):
            if str(tag.get("label", "")).lower() == label.lower():
                return tag["id"]
        return self.post("/api/v3/tag", {"label": label})["id"]

    def close(self) -> None:
        self._http.close()
