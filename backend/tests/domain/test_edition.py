import pytest

from overlaparr.domain.edition import compare_edition, normalize_edition


@pytest.mark.parametrize(
    "s,expected",
    [("Director's Cut", "directors"), ("Theatrical Cut", "theatrical"), ("Extended Edition", "extended"),
     ("  ", None), (None, None), ("Cut", None), ("Final   Cut!", "final")],
)
def test_normalize_edition(s, expected):
    assert normalize_edition(s) == expected


def test_theatrical_equal():
    assert normalize_edition("Theatrical") == normalize_edition("Theatrical Cut")


@pytest.mark.parametrize(
    "a,b,expected",
    [("Extended Edition", "extended", "same"), ("Directors Cut", "Theatrical", "different"),
     (None, "Theatrical", "unknown"), ("Theatrical", None, "unknown"), ("", "x", "unknown")],
)
def test_compare_edition(a, b, expected):
    assert compare_edition(a, b) == expected
