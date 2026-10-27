import pytest

from overlaparr.automation.rules import RuleMatch, evaluate, preview
from overlaparr.config.schema import Rule
from overlaparr.db.models import Finding, FindingCopy, FindingServerIgnore
from overlaparr.db.session import init_db, make_engine, make_sessionmaker


@pytest.fixture
def db(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    with make_sessionmaker(eng)() as s:
        yield s
    eng.dispose()


def rule(**kw) -> Rule:
    return Rule(id="r1", name="R", **kw)


def finding(**kw) -> Finding:
    base = dict(
        id=1,
        instance_id="radarr1",
        arr_id=10,
        kind="movie",
        type="wanted_elsewhere",
        review_state="new",
        lifecycle="active",
        automation_hold=False,
        relevant_episode_ids=[],
        coverage="full",
        best_quality="same",
        edition_summary="same",
        first_seen_scan_id=1,
        last_seen_scan_id=1,
    )
    base.update(kw)
    return Finding(**base)


def series(**kw) -> Finding:
    base = dict(instance_id="sonarr1", kind="series", relevant_episode_ids=[101, 102])
    base.update(kw)
    return finding(**base)


def copy(server="s1", episode=None, **kw) -> FindingCopy:
    base = dict(
        finding_id=1,
        server_id=server,
        episode_arr_id=episode,
        quality="same",
        edition_cmp="same",
        matched_by="id",
        stale=False,
    )
    base.update(kw)
    return FindingCopy(**base)


def ev(r, f, copies, ignored=frozenset()):
    return evaluate(r, f, copies, set(ignored), list(f.relevant_episode_ids))


def test_default_rule_matches_tv_despite_edition():
    f = series()
    copies = [copy("s1", 101, edition_cmp="unknown"), copy("s1", 102, edition_cmp="unknown")]
    assert ev(rule(), f, copies) == RuleMatch(finding_id=1, episode_ids=[101, 102], servers=["s1"])


def test_default_rule_matches_wanted_movie_despite_unknown_edition():
    f = finding(type="wanted_elsewhere")
    assert ev(rule(), f, [copy("s1", edition_cmp="unknown")]) == RuleMatch(1, [], ["s1"])


def test_default_rule_rejects_different_edition_movie():
    r = rule(finding_types=["on_disk"])
    f = finding(type="on_disk")
    assert ev(r, f, [copy("s1", edition_cmp="different")]) is None
    assert ev(r, f, [copy("s1", edition_cmp="same")]) == RuleMatch(1, [], ["s1"])
    assert ev(rule(finding_types=["on_disk"], edition=["different"]), f,
              [copy("s1", edition_cmp="different")]) == RuleMatch(1, [], ["s1"])


def test_720p_does_not_cover_1080p():
    f = series()
    copies = [copy("s1", 101, quality="same"), copy("s1", 102, quality="worse")]
    assert ev(rule(), f, copies) is None
    assert ev(rule(coverage="partial"), f, copies) == RuleMatch(1, [101], ["s1"])
    assert ev(rule(quality=["better", "same", "worse"]), f, copies) == RuleMatch(
        1, [101, 102], ["s1"]
    )


def test_number_match_excluded_by_default():
    f = finding()
    assert ev(rule(), f, [copy("s1", matched_by="number")]) is None
    assert ev(rule(match_methods=["id", "number"]), f, [copy("s1", matched_by="number")]) == (
        RuleMatch(1, [], ["s1"])
    )


def test_min_servers():
    f = finding()
    copies = [copy("s1"), copy("s1"), copy("s2", quality="worse")]
    assert ev(rule(min_servers=2), f, copies) is None
    copies.append(copy("s3"))
    assert ev(rule(min_servers=2), f, copies) == RuleMatch(1, [], ["s1", "s3"])

    s = series()
    sc = [copy("s1", 101), copy("s2", 101), copy("s1", 102)]
    assert ev(rule(min_servers=2), s, sc) is None
    assert ev(rule(min_servers=2, coverage="partial"), s, sc) == RuleMatch(1, [101], ["s1", "s2"])


def test_stale_copies_never_qualify():
    f = finding()
    assert ev(rule(), f, [copy("s1", stale=True)]) is None
    s = series()
    assert ev(rule(), s, [copy("s1", 101), copy("s1", 102, stale=True)]) is None


def test_ignored_server_excluded():
    f = finding()
    assert ev(rule(), f, [copy("s1")], ignored={"s1"}) is None
    assert ev(rule(), f, [copy("s1"), copy("s2")], ignored={"s1"}) == RuleMatch(1, [], ["s2"])


def test_rule_servers_filter():
    f = finding()
    assert ev(rule(servers=["s2"]), f, [copy("s1")]) is None
    assert ev(rule(servers=["s2"]), f, [copy("s1"), copy("s2")]) == RuleMatch(1, [], ["s2"])


def test_hold_blocks():
    assert ev(rule(), finding(automation_hold=True), [copy("s1")]) is None


@pytest.mark.parametrize(
    "kw",
    [
        {"lifecycle": "lost"},
        {"lifecycle": "removed"},
        {"lifecycle": "resolved"},
        {"review_state": "ignored"},
        {"type": "on_disk"},
    ],
)
def test_finding_level_rejections(kw):
    assert ev(rule(), finding(**kw), [copy("s1")]) is None


def test_instance_filter():
    assert ev(rule(instances=["other"]), finding(), [copy("s1")]) is None
    assert ev(rule(instances=["radarr1"]), finding(), [copy("s1")]) == RuleMatch(1, [], ["s1"])


def test_partial_returns_only_covered_episodes():
    f = series(relevant_episode_ids=[101, 102, 103])
    copies = [copy("s2", 103), copy("s1", 101), copy("s1", 999)]
    assert ev(rule(), f, copies) is None
    assert ev(rule(coverage="partial"), f, copies) == RuleMatch(1, [101, 103], ["s1", "s2"])


def test_series_without_relevant_episodes_never_matches():
    f = series(relevant_episode_ids=[])
    assert ev(rule(), f, [copy("s1", 101)]) is None
    assert ev(rule(coverage="partial"), f, [copy("s1", 101)]) is None


def test_preview_loads_active_findings_with_copies_and_ignores(db):
    movie = finding(id=None)
    show = series(id=None, arr_id=20)
    lost = finding(id=None, arr_id=30, lifecycle="lost")
    db.add_all([movie, show, lost])
    db.flush()
    db.add_all(
        [
            copy("s1", finding_id=movie.id),
            copy("s2", finding_id=movie.id),
            copy("s1", 101, finding_id=show.id),
            copy("s1", 102, finding_id=show.id),
            copy("s1", finding_id=lost.id),
            FindingServerIgnore(finding_id=movie.id, server_id="s1"),
        ]
    )
    db.commit()
    assert preview(db, rule()) == [
        RuleMatch(movie.id, [], ["s2"]),
        RuleMatch(show.id, [101, 102], ["s1"]),
    ]
