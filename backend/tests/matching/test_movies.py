from overlaparr.db.models import IndexItem, TrackedItem
from overlaparr.domain.guids import ExternalIds
from overlaparr.domain.quality import Res
from overlaparr.matching.movies import MovieIndex, match_movies

ONLINE = {"s1": "online", "s2": "online"}


def idx(server="s1", tmdb=None, imdb=None, res=Res.R2160, edition=None, title="X", kind="movie",
        hdr=True, codec="hevc", size=100):
    return IndexItem(
        server_id=server, kind=kind, rating_key=f"{server}-{tmdb}-{imdb}-{title}", title=title,
        tmdb=tmdb, imdb=imdb, resolution=int(res) if res is not None else None, hdr=hdr,
        codec=codec, edition=edition, size=size,
    )


def tracked(arr_id=1, tmdb=10, imdb=None, monitored=True, has_file=False, resolution=None,
            cutoff=Res.R1080, edition=None, tags=None, kind="movie", title="X"):
    return TrackedItem(
        instance_id="radarr", arr_id=arr_id, kind=kind, title=title, tmdb=tmdb, imdb=imdb,
        monitored=monitored, has_file=has_file,
        resolution=int(resolution) if resolution is not None else None,
        cutoff_resolution=int(cutoff) if cutoff is not None else None,
        edition=edition, tag_ids=tags or [], seen_scan_id=1,
    )


def run(items, rows, status=ONLINE, ignore=None, exclude=frozenset()):
    return match_movies(items, MovieIndex.build(rows, exclude), status, ignore)


def test_tmdb_match():
    [f] = run([tracked(tmdb=10)], [idx(tmdb=10)])
    assert (f.instance_id, f.arr_id, f.kind, f.type) == ("radarr", 1, "movie", "wanted_elsewhere")
    assert f.relevant_episode_ids == []
    [c] = f.copies
    assert c.server_id == "s1" and c.matched_by == "id" and c.episode_arr_id is None
    assert c.resolution == Res.R2160 and c.hdr and c.codec == "hevc" and c.size == 100
    assert c.quality == "better" and not c.stale


def test_imdb_fallback_only_when_no_tmdb():
    index = MovieIndex.build([
        idx("s1", tmdb=10, imdb="tt1", title="a"),
        idx("s2", tmdb=None, imdb="tt1", title="b"),
    ])
    hits = index.lookup(ExternalIds(tmdb=10, imdb="tt1"))
    assert set(hits) == {"s1", "s2"}
    assert hits["s1"].title == "a" and hits["s2"].title == "b"
    # server with a different TMDB id for the same imdb: TMDB hit wins, no imdb fallback
    index = MovieIndex.build([
        idx("s1", tmdb=10, imdb="tt1", title="a"),
        idx("s1", tmdb=99, imdb="tt1", title="b"),
    ])
    assert index.lookup(ExternalIds(tmdb=10, imdb="tt1"))["s1"].title == "a"
    # no TMDB hit: IMDb used
    assert index.lookup(ExternalIds(tmdb=555, imdb="tt1"))["s1"].imdb == "tt1"


def test_title_never_used():
    assert run([tracked(tmdb=10, title="Same")], [idx(tmdb=11, title="Same")]) == []


def test_non_movie_index_rows_ignored():
    assert run([tracked(tmdb=10)], [idx(tmdb=10, kind="show")]) == []


def test_non_movie_tracked_skipped():
    assert run([tracked(tmdb=10, kind="series")], [idx(tmdb=10)]) == []


def test_wanted_compares_to_cutoff():
    [f] = run([tracked(cutoff=Res.R1080)], [idx("s1", tmdb=10, res=Res.R720), idx("s2", tmdb=10, res=Res.R2160)])
    q = {c.server_id: c.quality for c in f.copies}
    assert q == {"s1": "worse", "s2": "better"}
    assert all(c.edition_cmp == "unknown" for c in f.copies)


def test_on_disk_edition_flag():
    t = tracked(has_file=True, resolution=Res.R1080, edition="Theatrical")
    [f] = run([t], [idx(tmdb=10, res=Res.R1080, edition="Director's Cut")])
    assert f.type == "on_disk"
    c = f.copies[0]
    assert c.quality == "same" and c.edition_cmp == "different" and c.edition == "Director's Cut"
    [f] = run([t], [idx(tmdb=10, res=Res.R1080, edition="Theatrical Edition")])
    assert f.copies[0].edition_cmp == "same"


def test_unmonitored_missing_ignored():
    assert run([tracked(monitored=False, has_file=False)], [idx(tmdb=10)]) == []


def test_unmonitored_with_file_is_on_disk():
    [f] = run([tracked(monitored=False, has_file=True, resolution=Res.R720)], [idx(tmdb=10)])
    assert f.type == "on_disk"


def test_no_copies_no_finding():
    assert run([tracked(tmdb=10)], []) == []


def test_stale_server_copy_flagged():
    status = {"s1": "offline", "s2": "degraded"}
    [f] = run([tracked()], [idx("s1", tmdb=10), idx("s2", tmdb=10), idx("s3", tmdb=10)], status)
    stale = {c.server_id: c.stale for c in f.copies}
    assert stale == {"s1": True, "s2": False, "s3": True}


def test_home_server_rows_excluded():
    rows = [idx("home", tmdb=10), idx("s1", tmdb=10)]
    [f] = run([tracked()], rows, exclude={"home"})
    assert [c.server_id for c in f.copies] == ["s1"]
    assert run([tracked()], [idx("home", tmdb=10)], exclude={"home"}) == []


def test_ignore_tag_skipped():
    items = [tracked(arr_id=1, tags=[7]), tracked(arr_id=2, tags=[8])]
    out = run(items, [idx(tmdb=10)], ignore=7)
    assert [f.arr_id for f in out] == [2]
    assert len(run(items, [idx(tmdb=10)], ignore=None)) == 2
