from overlaparr.domain.types import ComputedCopy, ComputedFinding


def _copy(ep):
    return ComputedCopy("s", ep, None, False, None, None, None, "unknown", "unknown", "number", False)


def _f(kind, relevant, eps):
    return ComputedFinding("i", 1, kind, "wanted_elsewhere", relevant, [_copy(e) for e in eps])


def test_coverage_partial():
    f = _f("series", [1, 2, 3], [1, 2, None])
    assert f.covered_episode_ids == {1, 2}
    assert f.coverage == "partial"


def test_coverage_full_across_copies():
    assert _f("series", [1, 2], [1, 2]).coverage == "full"
    assert _f("series", [1, 2], [1, 1, 2]).coverage == "full"


def test_movie_always_full():
    assert _f("movie", [], [None]).coverage == "full"
