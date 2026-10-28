"""Parsing of Plex GUIDs into external ids."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalIds:
    tmdb: int | None = None
    tvdb: int | None = None
    imdb: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.tmdb is None and self.tvdb is None and self.imdb is None


_GUID = re.compile(r"^(tmdb|tvdb|imdb)://(.+)$")
_IMDB = re.compile(r"^tt[0-9]+$")


def parse_guids(guids: Iterable[str]) -> ExternalIds:
    tmdb = tvdb = None
    imdb = None
    for guid in guids:
        if not isinstance(guid, str):
            continue
        m = _GUID.match(guid)
        if not m:
            continue
        scheme, value = m.groups()
        if scheme == "imdb":
            if _IMDB.match(value):
                imdb = value
        elif value.isascii() and value.isdigit():  # "²".isdigit() is True; int() rejects it
            if scheme == "tmdb":
                tmdb = int(value)
            else:
                tvdb = int(value)
    return ExternalIds(tmdb, tvdb, imdb)
