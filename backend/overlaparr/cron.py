"""Dependency-light cron helpers shared by the config schema and the scheduler."""

from __future__ import annotations

from datetime import datetime, tzinfo


def _trigger(expr: str, tz: tzinfo | None = None):
    from apscheduler.triggers.cron import CronTrigger

    try:
        return CronTrigger.from_crontab(expr, timezone=tz)
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"invalid cron expression {expr!r}: {e}") from e


def validate_cron(expr: str) -> str:
    _trigger(expr)
    return expr


def next_runs(cron: str, n: int = 5, tz: tzinfo | None = None) -> list[datetime]:
    trigger = _trigger(cron, tz)
    out: list[datetime] = []
    prev: datetime | None = None
    now = datetime.now(trigger.timezone)
    while len(out) < n:
        nxt = trigger.get_next_fire_time(prev, now if prev is None else prev)
        if nxt is None:
            break
        out.append(nxt)
        prev = nxt
    return out
