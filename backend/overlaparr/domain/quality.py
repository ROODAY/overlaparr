"""Resolution ranking and comparison."""

from __future__ import annotations

from enum import IntEnum
from typing import Literal


class Res(IntEnum):
    SD = 0
    R480 = 1
    R576 = 2
    R720 = 3
    R1080 = 4
    R2160 = 5


QualityCmp = Literal["better", "same", "worse", "unknown"]

_PLEX = {
    "sd": Res.SD,
    "480": Res.R480,
    "576": Res.R576,
    "720": Res.R720,
    "1080": Res.R1080,
    "4k": Res.R2160,
    "2160": Res.R2160,
}

_HEIGHTS = [(2160, Res.R2160), (1080, Res.R1080), (720, Res.R720), (576, Res.R576), (480, Res.R480)]


def parse_plex_resolution(s: str | None) -> Res | None:
    if not s:
        return None
    return _PLEX.get(s.strip().lower())


def res_from_height(h: int | None) -> Res | None:
    if not h or h <= 0:
        return None
    for floor, res in _HEIGHTS:
        if h >= floor:
            return res
    return Res.SD


def compare_quality(theirs: Res | None, yours: Res | None) -> QualityCmp:
    if theirs is None or yours is None:
        return "unknown"
    if theirs > yours:
        return "better"
    if theirs < yours:
        return "worse"
    return "same"


def _item_resolution(item: dict) -> Res | None:
    """Best resolution of an item: a leaf quality or a group of them."""
    best: Res | None = None
    quality = item.get("quality")
    if quality is not None:
        best = res_from_height(quality.get("resolution"))
    for sub in item.get("items") or []:
        r = _item_resolution(sub)
        if r is not None and (best is None or r > best):
            best = r
    return best


def _find_cutoff(items: list[dict], cutoff: int) -> Res | None:
    for item in items:
        quality = item.get("quality")
        item_id = quality.get("id") if quality is not None else item.get("id")
        if item_id == cutoff:
            return _item_resolution(item)
        found = _find_cutoff(item.get("items") or [], cutoff)
        if found is not None:
            return found
    return None


def profile_cutoff_resolution(profile: dict) -> Res | None:
    cutoff = profile.get("cutoff")
    if cutoff is None:
        return None
    return _find_cutoff(profile.get("items") or [], cutoff)
