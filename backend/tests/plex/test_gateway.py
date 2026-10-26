from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest

from overlaparr.plex.gateway import (
    PlexApiConnection,
    PlexApiGateway,
    PlexConnInfo,
    PlexServerInfo,
    PlexUnreachable,
    order_connections,
)
from tests.fakes import FakePlexGateway


def _resource(provides, cid="m1", name="S", conns=(), token="tok-secret"):
    return NS(
        provides=provides,
        clientIdentifier=cid,
        name=name,
        owned=True,
        accessToken=token,
        connections=[NS(uri=u, local=loc, relay=rel) for u, loc, rel in conns],
    )


def test_list_servers_filters_non_servers():
    res = [
        _resource("server", "a", "A", [("http://a:1", True, False)]),
        _resource("player,client", "b", "B"),
        _resource("server,player", "c", "C"),
    ]
    with patch("overlaparr.plex.gateway.MyPlexAccount") as acct:
        acct.return_value.resources.return_value = res
        servers = PlexApiGateway("t").list_servers()
    assert [s.machine_id for s in servers] == ["a", "c"]
    assert servers[0].connections == [PlexConnInfo("http://a:1", True, False)]


def _server():
    return PlexServerInfo(
        "m1",
        "S",
        True,
        [
            PlexConnInfo("http://relay", False, True),
            PlexConnInfo("http://remote", False, False),
            PlexConnInfo("http://local", True, False),
        ],
    )


def test_order_connections():
    assert [c.uri for c in order_connections(_server().connections)] == [
        "http://local",
        "http://remote",
        "http://relay",
    ]


def test_connect_tries_local_then_remote_then_relay():
    gw = PlexApiGateway("t")
    gw._access_tokens["m1"] = "acc"
    plex = MagicMock()
    with patch("overlaparr.plex.gateway.PlexServer", side_effect=[OSError("a"), OSError("b"), plex]) as ps:
        conn = gw.connect(_server(), timeout=3)
    assert [c.args[0] for c in ps.call_args_list] == ["http://local", "http://remote", "http://relay"]
    assert ps.call_args.args[1] == "acc"
    assert ps.call_args.kwargs["timeout"] == 3
    assert conn.via_relay is True


def test_connect_non_relay_not_flagged():
    gw = PlexApiGateway("t")
    gw._access_tokens["m1"] = "acc"
    with patch("overlaparr.plex.gateway.PlexServer", return_value=MagicMock()):
        assert gw.connect(_server(), timeout=3).via_relay is False


def test_connect_all_fail_raises_unreachable_without_tokens():
    srv = PlexServerInfo("m1", "Srv", True, [PlexConnInfo("http://h:1/x?X-Plex-Token=SECRET", False, False)])
    gw = PlexApiGateway("t")
    gw._access_tokens["m1"] = "acc"
    with patch("overlaparr.plex.gateway.PlexServer", side_effect=OSError("boom")):
        with pytest.raises(PlexUnreachable) as ei:
            gw.connect(srv, timeout=1)
    msg = str(ei.value)
    assert "Srv" in msg and "http://h:1/x" in msg
    assert "SECRET" not in msg and "X-Plex-Token" not in msg


def _media(res, size, trc=None, dovi=False, codec="hevc"):
    stream = NS(colorTrc=trc, DOVIPresent=dovi)
    part = NS(size=size, videoStreams=lambda: [stream])
    return NS(videoResolution=res, parts=[part], videoCodec=codec)


def _item(media, **kw):
    base = dict(
        ratingKey=10,
        title="T",
        year=2020,
        guids=[NS(id="tmdb://1"), NS(id="imdb://tt1")],
        media=media,
        editionTitle="Extended",
    )
    base.update(kw)
    return NS(**base)


def _conn_with_section(items_pages, stype="movie"):
    section = MagicMock()
    section.type = stype
    section.search.side_effect = items_pages
    server = MagicMock()
    server.library.sectionByID.return_value = section
    return PlexApiConnection(server, False), section


def test_best_media_selected():
    item = _item([_media("720", 5), _media("4k", 50, trc="smpte2084"), _media("1080", 900)])
    conn, _ = _conn_with_section([[item]])
    (page,) = list(conn.iter_items("1", 10))
    row = page[0]
    assert row.resolution == "4k"
    assert row.hdr is True
    assert row.size == 50
    assert row.codec == "hevc"
    assert row.edition == "Extended"
    assert row.guids == ["tmdb://1", "imdb://tt1"]
    assert row.rating_key == "10" and row.kind == "movie"


def test_best_media_tie_broken_by_size_and_dovi_hdr():
    item = _item([_media("1080", 5), _media("1080", 9, dovi=True)])
    conn, _ = _conn_with_section([[item]])
    row = next(conn.iter_items("1", 10))[0]
    assert row.size == 9 and row.hdr is True


def test_no_media_yields_empty_quality():
    conn, _ = _conn_with_section([[_item([], editionTitle=None)]])
    row = next(conn.iter_items("1", 10))[0]
    assert (row.resolution, row.hdr, row.codec, row.size, row.edition) == (None, False, None, None, None)


def test_iter_items_pages_with_verified_kwargs():
    p1 = [_item([_media("1080", 1)], ratingKey=i) for i in (1, 2)]
    p2 = [_item([_media("1080", 1)], ratingKey=3)]
    conn, section = _conn_with_section([p1, p2])
    pages = list(conn.iter_items("1", 2))
    assert [[r.rating_key for r in p] for p in pages] == [["1", "2"], ["3"]]
    first, second = section.search.call_args_list
    assert first.kwargs == dict(container_start=0, container_size=2, maxresults=2, includeGuids=True)
    assert second.kwargs["container_start"] == 2


def test_iter_items_stops_on_empty_page():
    full = [_item([_media("1080", 1)], ratingKey=i) for i in (1, 2)]
    conn, _ = _conn_with_section([full, []])
    assert len(list(conn.iter_items("1", 2))) == 1


def test_sections_filters_types():
    server = MagicMock()
    server.library.sections.return_value = [
        NS(key=1, type="movie"),
        NS(key=2, type="artist"),
        NS(key=3, type="show"),
    ]
    assert PlexApiConnection(server, False).sections() == [("1", "movie"), ("3", "show")]


def test_episodes_single_call_with_guids():
    ep = NS(
        parentIndex=1,
        index=2,
        guids=[NS(id="tvdb://9")],
        media=[_media("1080", 7)],
    )
    server = MagicMock()
    server.fetchItems.return_value = [ep]
    rows = PlexApiConnection(server, False).episodes("55")
    assert server.fetchItems.call_count == 1
    assert "/library/metadata/55/allLeaves" in server.fetchItems.call_args.args[0]
    assert "includeGuids=1" in server.fetchItems.call_args.args[0]
    r = rows[0]
    assert (r.show_rating_key, r.season, r.episode, r.guids, r.resolution, r.size) == (
        "55",
        1,
        2,
        ["tvdb://9"],
        "1080",
        7,
    )


def test_fake_gateway_fail_after_pages_and_unreachable():
    from overlaparr.plex.gateway import PlexItemRow

    items = [PlexItemRow(str(i), "movie", "t", None, [], None, False, None, None, None) for i in range(5)]
    gw = FakePlexGateway(
        {"A": {"items": items, "fail_after_pages": 1}, "B": {"unreachable": True}, "C": {"items": items}}
    )
    a, b, c = gw.list_servers()
    conn = gw.connect(a, 1)
    assert conn.sections() == [("movie", "movie")]
    it = conn.iter_items("movie", 2)
    assert len(next(it)) == 2
    with pytest.raises(PlexUnreachable):
        next(it)
    with pytest.raises(PlexUnreachable):
        gw.connect(b, 1)
    assert sum(len(p) for p in gw.connect(c, 1).iter_items("movie", 2)) == 5
