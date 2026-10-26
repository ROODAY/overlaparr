"""Edition-name normalisation and comparison."""

from __future__ import annotations

import re
from typing import Literal

EditionCmp = Literal["same", "different", "unknown"]

_TRAILING = {"cut", "edition", "version"}


def normalize_edition(s: str | None) -> str | None:
    if not s:
        return None
    text = s.lower().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^\w\s]", " ", text)
    words = text.split()
    while words and words[-1] in _TRAILING:
        words.pop()
    return " ".join(words) or None


def compare_edition(theirs: str | None, yours: str | None) -> EditionCmp:
    a, b = normalize_edition(theirs), normalize_edition(yours)
    if a is None or b is None:
        return "unknown"
    return "same" if a == b else "different"
