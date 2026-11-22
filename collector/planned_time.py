#!/usr/bin/env python3
"""Resolve GitHub schedule cron text to an Asia/Shanghai timestamp.

github.event.schedule is the cron expression that fired, not a timestamp.
workflow_dispatch has no schedule slot; planned_at stays blank.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def timezone_for(name: str) -> dt.tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    if name == "Asia/Shanghai":
        return dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
    raise SystemExit(f"unsupported timezone: {name}")


def _expand(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        base = part
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"bad step in {field}")
        if base in {"*", ""}:
            start, end = low, high
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        if start < low or end > high or start > end:
            raise ValueError(f"field out of range: {field}")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron must have 5 fields, got {expr!r}")
    minute, hour, dom, month, dow = fields
    return (
        _expand(minute, 0, 59),
        _expand(hour, 0, 23),
        _expand(dom, 1, 31),
        _expand(month, 1, 12),
        _expand(dow, 0, 6),
    )


def matches(expr: str, when: dt.datetime) -> bool:
    minute, hour, dom, month, dow = parse_cron(expr)
    # GitHub dow: 0 or SUN is Sunday. Python weekday(): Monday=0 ... Sunday=6.
    github_dow = (when.weekday() + 1) % 7
    dom_any = expr.split()[2] == "*"
    dow_any = expr.split()[4] == "*"
    date_ok = True
    if not dom_any and not dow_any:
        # POSIX cron: if both day-of-month and day-of-week are restricted, either may match.
        date_ok = when.day in dom or github_dow in dow
    else:
        date_ok = (dom_any or when.day in dom) and (dow_any or github_dow in dow)
    return (
        when.minute in minute
        and when.hour in hour
        and when.month in month
        and date_ok
    )


def most_recent(expr: str, now: dt.datetime, tz: dt.tzinfo, lookback_minutes: int = 48 * 60) -> dt.datetime:
    cursor = now.astimezone(tz).replace(second=0, microsecond=0)
    for _ in range(lookback_minutes + 1):
        if matches(expr, cursor):
            return cursor
        cursor -= dt.timedelta(minutes=1)
    raise ValueError(f"no cron match for {expr!r} within {lookback_minutes} minutes before {now.isoformat()}")


def resolve(event_name: str, schedule: str, tz_name: str, now: dt.datetime) -> tuple[str, str]:
    if event_name != "schedule":
        return "", "not_scheduled"
    expr = (schedule or "").strip().strip("'\"")
    if not expr:
        raise ValueError("schedule event missing github.event.schedule")
    tz = timezone_for(tz_name)
    slot = most_recent(expr, now, tz)
    return slot.isoformat(), "schedule_timestamp"


def _write_output(planned_at: str, kind: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    line = f"planned_at={planned_at}\nplanned_kind={kind}\n"
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    print(line, end="")


def self_test() -> None:
    tz = timezone_for("Asia/Shanghai")
    expr = "30 10 23,24,25 11 *"
    # 2026-11-22 10:30 CST is Sunday and is NOT one of the acceptance dates.
    missed = dt.datetime(2026, 11, 22, 12, 38, tzinfo=tz)
    assert matches(expr, dt.datetime(2026, 11, 22, 10, 30, tzinfo=tz)) is False
    try:
        most_recent(expr, missed, tz, lookback_minutes=180)
        raise AssertionError("expired 11/22 slot must not resolve")
    except ValueError:
        pass
    fired = dt.datetime(2026, 11, 23, 10, 36, tzinfo=tz)
    slot, kind = resolve("schedule", expr, "Asia/Shanghai", fired)
    assert slot == "2026-11-23T10:30:00+08:00", slot
    assert kind == "schedule_timestamp"
    late = dt.datetime(2026, 11, 25, 11, 5, tzinfo=tz)
    slot, _ = resolve("schedule", expr, "Asia/Shanghai", late)
    assert slot == "2026-11-25T10:30:00+08:00", slot
    weekly = "30 10 * * 0"
    sunday = dt.datetime(2026, 11, 29, 10, 30, tzinfo=tz)
    assert matches(weekly, sunday) is True
    assert matches(weekly, dt.datetime(2026, 11, 23, 10, 30, tzinfo=tz)) is False
    blank, kind = resolve("workflow_dispatch", "30 10 23,24,25 11 *", "Asia/Shanghai", fired)
    assert blank == "" and kind == "not_scheduled"
    # Cron text itself is not a timestamp.
    from ledger import planned_column

    assert planned_column("30 10 23,24,25 11 *") == ""
    assert planned_column("manual") == ""
    assert planned_column(slot).startswith("2026-11-25T10:30:00")
    print("planned_time self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", default="workflow_dispatch")
    parser.add_argument("--schedule", default="")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--now", default="", help="ISO time override, for tests")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(timezone_for(args.timezone))
    try:
        planned_at, kind = resolve(args.event_name, args.schedule, args.timezone, now)
    except Exception as exc:  # noqa: BLE001
        print(f"planned_time_error={exc}", file=sys.stderr)
        return 2
    _write_output(planned_at, kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
