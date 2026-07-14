"""Pure, testable logic for the pre-market "day setup": the overnight gap, the
prior-day / overnight levels that become intraday support/resistance, today's
scheduled catalysts, and an opening-bias read. Like signals/indicators.py these
take plain numbers / dicts (never DataFrames or a DB handle) so the worker does
the I/O and these stay easy to unit test.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def opening_bias_score(gap_pct: float | None, scale_pct: float = 0.5) -> float | None:
    """A -1..1 lean from the overnight gap: a bigger up-gap reads as a stronger
    bullish opening bias (and vice versa). Context only - deliberately NOT folded
    into the weighted composite, which already scores direction. None when the
    gap is unknown."""
    if gap_pct is None or scale_pct <= 0:
        return None
    return _clip(gap_pct / scale_pct)


def gap_pct(premarket_quote: float | None, prior_close: float | None) -> float | None:
    """Overnight gap as a percent of the prior close. None if either leg is
    missing or the prior close is zero."""
    if premarket_quote is None or prior_close is None or prior_close == 0:
        return None
    return (premarket_quote - prior_close) / prior_close * 100.0


def catalysts_for_date(
    catalysts_cfg: list[dict] | None, target_date: date, tz_name: str = "America/New_York",
) -> list[dict]:
    """The configured catalysts (settings.yaml market_catalysts) that fall on
    target_date, each normalized to {time, label, impact}. Order preserved."""
    result = []
    for entry in catalysts_cfg or []:
        if entry.get("date") == target_date.isoformat():
            result.append({
                "time": entry.get("time"),
                "label": entry.get("label", "event"),
                "impact": entry.get("impact", "high"),
            })
    return result


def minutes_to_next_catalyst(
    catalysts_today: list[dict] | None, now: datetime, tz_name: str = "America/New_York",
) -> float | None:
    """Minutes until the next high-impact catalyst still ahead today, or None if
    there are none ahead. `now` is tz-aware; catalyst times are HH:MM in the
    market tz on the same date as `now`."""
    if not catalysts_today:
        return None
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    upcoming = []
    for entry in catalysts_today:
        if entry.get("impact") != "high" or not entry.get("time"):
            continue
        try:
            hh, mm = (int(p) for p in entry["time"].split(":"))
        except (ValueError, AttributeError):
            continue
        event_dt = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta_min = (event_dt - local_now).total_seconds() / 60.0
        if delta_min >= 0:
            upcoming.append(delta_min)
    return min(upcoming) if upcoming else None


def build_day_setup(
    ticker: str,
    gap_percent: float | None,
    prior_high: float | None,
    prior_low: float | None,
    prior_close: float | None,
    overnight_high: float | None,
    overnight_low: float | None,
    catalysts_today: list[dict] | None,
    scale_pct: float = 0.5,
) -> dict:
    """Assembles the per-ticker setup dict persisted in day_setups. Any field may
    be None (a partial setup is still useful - e.g. levels without a gap when the
    overnight quote is unavailable)."""
    if gap_percent is None:
        gap_direction = None
    elif gap_percent > 0.05:
        gap_direction = "up"
    elif gap_percent < -0.05:
        gap_direction = "down"
    else:
        gap_direction = "flat"
    return {
        "ticker": ticker,
        "gap_pct": gap_percent,
        "gap_direction": gap_direction,
        "prior_high": prior_high,
        "prior_low": prior_low,
        "prior_close": prior_close,
        "overnight_high": overnight_high,
        "overnight_low": overnight_low,
        "catalysts": list(catalysts_today or []),
        "opening_bias": opening_bias_score(gap_percent, scale_pct),
    }
