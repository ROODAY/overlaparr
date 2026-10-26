"""Boundary layer over python-plexapi. Returns plain dataclasses only."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from plexapi.video import Episode

from overlaparr.domain.quality import parse_plex_resolution

log = logging.getLogger(__name__)

_HDR_TRC = {"smpte2084", "arib-std-b67"}


class PlexUnreachable(Exception):
    """No connection to a Plex server could be established."""


@dataclass
class PlexConnInfo:
    uri: str
    local: bool
    relay: bool


@dataclass
class PlexServerInfo:
    machine_id: str
    name: str
    owned: bool
    connections: list[PlexConnInfo] = field(default_factory=list)


@dataclass
class PlexItemRow:
    rating_key: str
    kind: Literal["movie", "show"]
    title: str
    year: int | None
    guids: list[str]
    resolution: str | None
    hdr: bool
    codec: str | None
    edition: str | None
    size: int | None


@dataclass
class PlexEpisodeRow:
    show_rating_key: str
    season: int | None
    episode: int | None
    guids: list[str]
    resolution: str | None
    hdr: bool
    codec: str | None
    size: int | None


class PlexConnection(Protocol):
    via_relay: bool

    def sections(self) -> list[tuple[str, Literal["movie", "show"]]]: ...

    def iter_items(self, section_key: str, page_size: int) -> Iterator[list[PlexItemRow]]: ...

    def episodes(self, show_rating_key: str) -> list[PlexEpisodeRow]: ...


class PlexGateway(Protocol):
    def list_servers(self) -> list[PlexServerInfo]: ...

    def connect(self, server: PlexServerInfo, timeout: float) -> PlexConnection: ...


def strip_query(uri: str) -> str:
    """Drop userinfo, query string and fragment (plexapi may embed tokens there)."""
    try:
        p = urlsplit(uri)
        return urlunsplit((p.scheme, p.netloc.rsplit("@", 1)[-1], p.path, "", ""))
    except ValueError:
        return uri.split("?", 1)[0]


def order_connections(conns: list[PlexConnInfo]) -> list[PlexConnInfo]:
    """local -> remote (non-local, non-relay) -> relay; plex.tv order kept within each class."""
    local = [c for c in conns if c.local and not c.relay]
    remote = [c for c in conns if not c.local and not c.relay]
    relay = [c for c in conns if c.relay]
    return local + remote + relay


def _media_size(media: Any) -> int:
    return sum((getattr(p, "size", None) or 0) for p in (getattr(media, "parts", None) or []))


def _media_rank(media: Any) -> tuple[int, int]:
    res = parse_plex_resolution(getattr(media, "videoResolution", None))
    return (int(res) if res is not None else -1, _media_size(media))


def _best_media(item: Any) -> Any | None:
    medias = list(getattr(item, "media", None) or [])
    if not medias:
        return None
    return max(medias, key=_media_rank)


def _is_hdr(media: Any) -> bool:
    for part in getattr(media, "parts", None) or []:
        for stream in part.videoStreams():
            if bool(getattr(stream, "DOVIPresent", False)):
                return True
            if (getattr(stream, "colorTrc", None) or "").lower() in _HDR_TRC:
                return True
    return False


def _guids(item: Any) -> list[str]:
    return [g.id for g in (getattr(item, "guids", None) or []) if getattr(g, "id", None)]


def _media_fields(item: Any) -> tuple[str | None, bool, str | None, int | None]:
    media = _best_media(item)
    if media is None:
        return None, False, None, None
    size = _media_size(media) or None
    return getattr(media, "videoResolution", None), _is_hdr(media), getattr(media, "videoCodec", None), size


class PlexApiConnection:
    def __init__(self, server: Any, via_relay: bool) -> None:
        self._server = server
        self.via_relay = via_relay

    def sections(self) -> list[tuple[str, Literal["movie", "show"]]]:
        out: list[tuple[str, Literal["movie", "show"]]] = []
        for s in self._server.library.sections():
            if s.type == "movie":
                out.append((str(s.key), "movie"))
            elif s.type == "show":
                out.append((str(s.key), "show"))
        return out

    def _item_row(self, item: Any, kind: Literal["movie", "show"]) -> PlexItemRow:
        resolution, hdr, codec, size = _media_fields(item)
        edition = getattr(item, "editionTitle", None) if kind == "movie" else None
        return PlexItemRow(
            rating_key=str(item.ratingKey),
            kind=kind,
            title=item.title,
            year=getattr(item, "year", None),
            guids=_guids(item),
            resolution=resolution,
            hdr=hdr,
            codec=codec,
            edition=edition or None,
            size=size,
        )

    def iter_items(self, section_key: str, page_size: int) -> Iterator[list[PlexItemRow]]:
        section = self._server.library.sectionByID(int(section_key))
        kind: Literal["movie", "show"] = "movie" if section.type == "movie" else "show"
        start = 0
        while True:
            # maxresults caps plexapi's own auto-paging so each call fetches exactly one page.
            items = section.search(
                container_start=start, container_size=page_size, maxresults=page_size, includeGuids=True
            )
            if not items:
                return
            yield [self._item_row(i, kind) for i in items]
            if len(items) < page_size:
                return
            start += page_size

    def episodes(self, show_rating_key: str) -> list[PlexEpisodeRow]:
        # allLeaves returns every episode of the show in one request.
        key = f"/library/metadata/{int(show_rating_key)}/allLeaves?includeGuids=1"
        rows = []
        for e in self._server.fetchItems(key, Episode):
            resolution, hdr, codec, size = _media_fields(e)
            rows.append(
                PlexEpisodeRow(
                    show_rating_key=str(show_rating_key),
                    season=getattr(e, "parentIndex", None),
                    episode=getattr(e, "index", None),
                    guids=_guids(e),
                    resolution=resolution,
                    hdr=hdr,
                    codec=codec,
                    size=size,
                )
            )
        return rows


class PlexApiGateway:
    def __init__(self, token: str) -> None:
        self._token = token
        self._access_tokens: dict[str, str | None] = {}

    def list_servers(self) -> list[PlexServerInfo]:
        account = MyPlexAccount(token=self._token)
        out: list[PlexServerInfo] = []
        for r in account.resources():
            if "server" not in (r.provides or ""):
                continue
            self._access_tokens[r.clientIdentifier] = r.accessToken
            out.append(
                PlexServerInfo(
                    machine_id=r.clientIdentifier,
                    name=r.name,
                    owned=bool(r.owned),
                    connections=[
                        PlexConnInfo(uri=c.uri, local=bool(c.local), relay=bool(c.relay)) for c in r.connections
                    ],
                )
            )
        return out

    def connect(self, server: PlexServerInfo, timeout: float) -> PlexConnection:
        if server.machine_id not in self._access_tokens:
            self.list_servers()
        access_token = self._access_tokens.get(server.machine_id) or self._token
        tried: list[str] = []
        for conn in order_connections(server.connections):
            tried.append(strip_query(conn.uri))
            try:
                plex = PlexServer(conn.uri, access_token, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - plexapi raises many types
                log.info("plex connect failed: server=%s uri=%s err=%s", server.name, tried[-1], type(exc).__name__)
                continue
            return PlexApiConnection(plex, via_relay=conn.relay)
        raise PlexUnreachable(
            f"cannot reach Plex server {server.name!r}; tried: {', '.join(tried) or 'no connections'}"
        )
