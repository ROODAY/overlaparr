from overlaparr.domain.guids import ExternalIds, parse_guids


def test_parse_guids():
    ids = parse_guids(["tmdb://603", "imdb://tt0133093", "tvdb://x", "plex://movie/5d77"])
    assert ids == ExternalIds(603, None, "tt0133093")
    assert not ids.is_empty


def test_parse_guids_ignores_legacy_and_malformed():
    assert parse_guids(["com.plexapp.agents.imdb://tt1?lang=en", "tmdb://", "junk", "imdb://12"]).is_empty


def test_parse_all():
    assert parse_guids(["tvdb://456", "tmdb://1"]) == ExternalIds(1, 456, None)


def test_parse_guids_non_ascii_digits_ignored():
    # "²" and Arabic-Indic digits pass str.isdigit() but int() rejects or misreads them
    ids = parse_guids(["tmdb://²", "tvdb://١٢٣", "imdb://tt١٢", "tmdb://603"])
    assert ids == ExternalIds(603, None, None)


def test_parse_guids_skips_non_string_entries():
    assert parse_guids([None, 5, "tvdb://7"]) == ExternalIds(None, 7, None)
