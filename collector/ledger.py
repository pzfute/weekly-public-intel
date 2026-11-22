"""Shared ledger fields. Planned time is an ISO timestamp or blank."""
from __future__ import annotations

import datetime as dt


def planned_column(value: str) -> str:
    """Return an ISO timestamp, or blank.

    github.event.schedule is the cron text, not a time. Cron text, the word
    "manual", and any non-timestamp must not be stored in planned_at.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if text.lower() in {"manual", "workflow_dispatch", "schedule"}:
        return ""
    if "T" not in text:
        return ""
    try:
        dt.datetime.fromisoformat(text)
    except ValueError:
        return ""
    return text
