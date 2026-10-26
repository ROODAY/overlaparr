import pytest

from overlaparr.domain.quality import (
    Res,
    compare_quality,
    parse_plex_resolution,
    profile_cutoff_resolution,
    res_from_height,
)


@pytest.mark.parametrize(
    "s,expected",
    [("4k", Res.R2160), ("SD", Res.SD), ("1080", Res.R1080), ("720", Res.R720),
     ("576", Res.R576), ("480", Res.R480), ("2160", Res.R2160), ("weird", None), (None, None)],
)
def test_parse_plex_resolution(s, expected):
    assert parse_plex_resolution(s) == expected


@pytest.mark.parametrize(
    "h,expected",
    [(2160, Res.R2160), (4320, Res.R2160), (1080, Res.R1080), (720, Res.R720), (576, Res.R576),
     (480, Res.R480), (0, None), (None, None), (360, Res.SD), (1000, Res.R720)],
)
def test_res_from_height(h, expected):
    assert res_from_height(h) == expected


@pytest.mark.parametrize(
    "a,b,expected",
    [(Res.R2160, Res.R1080, "better"), (Res.R720, Res.R1080, "worse"),
     (Res.R1080, Res.R1080, "same"), (None, Res.R1080, "unknown"), (Res.R1080, None, "unknown")],
)
def test_compare_quality(a, b, expected):
    assert compare_quality(a, b) == expected


def test_res_is_int():
    assert int(Res.R2160) == 5


def _q(id_, res):
    return {"quality": {"id": id_, "name": f"q{id_}", "resolution": res}}


def test_profile_cutoff_simple():
    p = {"cutoff": 7, "items": [{**_q(3, 720), "allowed": True}, {**_q(7, 1080), "allowed": True}]}
    assert profile_cutoff_resolution(p) == Res.R1080


def test_profile_cutoff_with_group():
    p = {
        "cutoff": 1001,
        "items": [
            {**_q(3, 720), "allowed": True},
            {"id": 1001, "name": "WEB 1080p", "items": [_q(15, 1080), _q(7, 1080)], "allowed": True},
        ],
    }
    assert profile_cutoff_resolution(p) == Res.R1080


def test_profile_cutoff_group_uses_max():
    p = {"cutoff": 1001, "items": [{"id": 1001, "items": [_q(1, 720), _q(2, 1080)]}]}
    assert profile_cutoff_resolution(p) == Res.R1080


def test_profile_cutoff_nested_quality_in_group_id():
    p = {"cutoff": 7, "items": [{"id": 1001, "items": [_q(7, 2160)]}]}
    assert profile_cutoff_resolution(p) == Res.R2160


def test_profile_cutoff_missing():
    assert profile_cutoff_resolution({"items": []}) is None
    assert profile_cutoff_resolution({"cutoff": 9, "items": [_q(1, 720)]}) is None
