"""Test doubles shared across test modules."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any, Literal

from overlaparr.plex.gateway import (
    PlexConnInfo,
    PlexEpisodeRow,
    PlexItemRow,
    PlexServerInfo,
    PlexUnreachable,
)


class FakePlexConnection:
    def __init__(self, name: str, cfg: dict[str, Any], delay: float) -> None:
        self._name = name
        self._cfg = cfg
        self._delay = delay
        self.via_relay: bool = bool(cfg.get("relay", False))

    def sections(self) -> list[tuple[str, Literal["movie", "show"]]]:
        kinds = {i.kind for i in self._cfg.get("items", [])}
        return [(k, k) for k in ("movie", "show") if k in kinds]  # type: ignore[misc]

    def iter_items(self, section_key: str, page_size: int) -> Iterator[list[PlexItemRow]]:
        items = [i for i in self._cfg.get("items", []) if i.kind == section_key]
        fail_after = self._cfg.get("fail_after_pages")
        for pages, start in enumerate(range(0, len(items), page_size)):
            if fail_after is not None and pages >= fail_after:
                raise PlexUnreachable(f"{self._name} dropped mid-scan")
            if self._delay:
                time.sleep(self._delay)
            yield items[start : start + page_size]

    def episodes(self, show_rating_key: str) -> list[PlexEpisodeRow]:
        return list(self._cfg.get("episodes", {}).get(show_rating_key, []))


class FakePlexGateway:
    """In-memory PlexGateway.

    ``servers`` maps server name -> config dict with optional keys:
      items: list[PlexItemRow]            (sections are derived from item kinds;
                                           the section key equals the kind)
      episodes: dict[show_rating_key, list[PlexEpisodeRow]]
      unreachable: bool                   connect() raises PlexUnreachable
      relay: bool                         connection reports via_relay=True
      fail_after_pages: int | None        iter_items raises PlexUnreachable after N pages
      owned: bool                         (default True)
    ``delay`` is seconds slept before yielding each page (index-budget tests).
    Server machine_id is ``"id-<name>"``.
    """

    def __init__(self, servers: dict[str, dict[str, Any]] | None = None, delay: float = 0.0) -> None:
        self.servers = servers or {}
        self.delay = delay
        self.connect_calls: list[str] = []

    def list_servers(self) -> list[PlexServerInfo]:
        return [
            PlexServerInfo(
                machine_id=f"id-{name}",
                name=name,
                owned=bool(cfg.get("owned", True)),
                connections=[PlexConnInfo(uri=f"http://{name}.test:32400", local=False, relay=False)],
            )
            for name, cfg in self.servers.items()
        ]

    def connect(self, server: PlexServerInfo, timeout: float) -> FakePlexConnection:
        self.connect_calls.append(server.name)
        cfg = self.servers.get(server.name)
        if cfg is None or cfg.get("unreachable"):
            raise PlexUnreachable(f"cannot reach Plex server {server.name!r}")
        return FakePlexConnection(server.name, cfg, self.delay)
