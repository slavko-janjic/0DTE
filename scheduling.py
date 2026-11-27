"""Exchange-time slots shared by the digest builder and the agent runner:
"run at these HH:MM times on trading days, and after downtime only catch up
on the latest missed one"."""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo


def latest_slot(times: list[str], config: dict, now: datetime) -> datetime | None:
    """The most recent of today's `times` that has passed (UTC), or None on a
    weekend/holiday or before the first slot."""
    tz = ZoneInfo(config.get("market_hours", {}).get("timezone", "America/New_York"))
    local = now.astimezone(tz)
    if local.weekday() >= 5 or local.date().isoformat() in set(config.get("market_holidays", [])):
        return None
    passed = []
    for t in times:
        hh, mm = (int(x) for x in str(t).split(":"))
        slot = datetime.combine(local.date(), dtime(hh, mm), tz)
        if slot <= local:
            passed.append(slot)
    return max(passed).astimezone(timezone.utc) if passed else None


def slot_due(times: list[str], config: dict, last_run: str | None, now: datetime) -> datetime | None:
    slot = latest_slot(times, config, now)
    if slot is None or (last_run and datetime.fromisoformat(last_run) >= slot):
        return None
    return slot
