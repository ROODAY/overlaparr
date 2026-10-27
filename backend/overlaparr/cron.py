"""Dependency-light cron helpers shared by the config schema and the scheduler.

Expressions use standard 5-field cron semantics (day-of-week 0 or 7 = Sunday);
APScheduler's own numeric day-of-week (0 = Monday) is normalised away.
"""

from __future__ import annotations

from datetime import datetime, tzinfo

_DOW = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def _dow_num(tok: str) -> int:
    t = tok.strip().lower()
    if t in _DOW:
        return _DOW.index(t)
    if not t.isdigit() or int(t) > 7:
        raise ValueError(f"invalid day-of-week {tok!r}")
    return int(t)


def _normalize_dow(field: str) -> str:
    if field == "*":
        return field
    days: set[int] = set()
    for part in field.split(","):
        base, sep, step_s = part.partition("/")
        step = 1
        if sep:
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"invalid step in day-of-week {part!r}")
            step = int(step_s)
        if base == "*":
            lo, hi = 0, 6
        elif "-" in base:
            a, _, b = base.partition("-")
            lo, hi = _dow_num(a), _dow_num(b)
            if hi < lo:  # wrapping range such as fri-mon
                hi += 7
        else:
            lo = _dow_num(base)
            hi = 6 if sep else lo
            if sep and hi < lo:
                hi = lo
        days.update(d % 7 for d in range(lo, hi + 1, step))
    if len(days) == 7:
        return "*"
    return ",".join(_DOW[d] for d in sorted(days))


def _normalize(expr: str) -> str:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have exactly 5 fields: {expr!r}")
    fields[4] = _normalize_dow(fields[4])
    return " ".join(fields)


def build_trigger(expr: str, tz: tzinfo | None = None):
    from apscheduler.triggers.cron import CronTrigger

    try:
        return CronTrigger.from_crontab(_normalize(expr), timezone=tz)
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"invalid cron expression {expr!r}: {e}") from e


def validate_cron(expr: str) -> str:
    build_trigger(expr)
    return expr


def next_runs(cron: str, n: int = 5, tz: tzinfo | None = None) -> list[datetime]:
    trigger = build_trigger(cron, tz)
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
