"""Pure rule evaluation against findings, plus a read-only preview."""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.config.schema import Rule
from overlaparr.db.models import Finding, FindingCopy, FindingServerIgnore


@dataclass
class RuleMatch:
    finding_id: int
    episode_ids: list[int]
    servers: list[str]


def _qualifies(rule: Rule, finding: Finding, c: FindingCopy, ignored: set[str]) -> bool:
    if c.stale or c.server_id in ignored:
        return False
    if rule.servers and c.server_id not in rule.servers:
        return False
    if c.quality not in rule.quality or c.matched_by not in rule.match_methods:
        return False
    # Edition only means something when there is a movie file to compare against.
    if finding.kind == "movie" and finding.type == "on_disk":
        return c.edition_cmp in rule.edition
    return True


def evaluate(
    rule: Rule,
    finding: Finding,
    copies: list[FindingCopy],
    ignored_servers: set[str],
    relevant_episode_ids: list[int],
) -> RuleMatch | None:
    if (
        finding.lifecycle != "active"
        or finding.review_state == "ignored"
        or finding.automation_hold
        or finding.type not in rule.finding_types
        or (rule.instances and finding.instance_id not in rule.instances)
    ):
        return None
    good = [c for c in copies if _qualifies(rule, finding, c, ignored_servers)]

    if finding.kind == "movie":
        servers = {c.server_id for c in good}
        if len(servers) < rule.min_servers:
            return None
        return RuleMatch(finding.id, [], sorted(servers))

    relevant = set(relevant_episode_ids)
    by_episode: dict[int, set[str]] = defaultdict(set)
    for c in good:
        if c.episode_arr_id in relevant:
            by_episode[c.episode_arr_id].add(c.server_id)
    covered = sorted(e for e, s in by_episode.items() if len(s) >= rule.min_servers)
    if not covered or (rule.coverage == "full" and len(covered) < len(relevant)):
        return None
    servers = set().union(*(by_episode[e] for e in covered))
    return RuleMatch(finding.id, covered, sorted(servers))


def preview(db: Session, rule: Rule) -> list[RuleMatch]:
    findings = list(
        db.scalars(select(Finding).where(Finding.lifecycle == "active").order_by(Finding.id))
    )
    ids = [f.id for f in findings]
    copies: dict[int, list[FindingCopy]] = defaultdict(list)
    for c in db.scalars(select(FindingCopy).where(FindingCopy.finding_id.in_(ids))):
        copies[c.finding_id].append(c)
    ignores: dict[int, set[str]] = defaultdict(set)
    ignore_q = select(FindingServerIgnore).where(FindingServerIgnore.finding_id.in_(ids))
    for i in db.scalars(ignore_q):
        ignores[i.finding_id].add(i.server_id)
    matches = []
    for f in findings:
        m = evaluate(rule, f, copies[f.id], ignores[f.id], list(f.relevant_episode_ids))
        if m is not None:
            matches.append(m)
    return matches
