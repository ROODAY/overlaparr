"""Computed overlap findings (pure data, no persistence)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from overlaparr.domain.edition import EditionCmp
from overlaparr.domain.quality import QualityCmp, Res

MatchMethod = Literal["id", "number"]
FindingType = Literal["wanted_elsewhere", "on_disk"]


@dataclass
class ComputedCopy:
    server_id: str
    episode_arr_id: int | None
    resolution: Res | None
    hdr: bool
    codec: str | None
    edition: str | None
    size: int | None
    quality: QualityCmp
    edition_cmp: EditionCmp
    matched_by: MatchMethod
    stale: bool


@dataclass
class ComputedFinding:
    instance_id: str
    arr_id: int
    kind: Literal["movie", "series"]
    type: FindingType
    relevant_episode_ids: list[int] = field(default_factory=list)
    copies: list[ComputedCopy] = field(default_factory=list)

    @property
    def covered_episode_ids(self) -> set[int]:
        return {c.episode_arr_id for c in self.copies if c.episode_arr_id is not None}

    @property
    def coverage(self) -> Literal["full", "partial"]:
        if self.kind == "movie":
            return "full"
        return "full" if set(self.relevant_episode_ids) <= self.covered_episode_ids else "partial"
