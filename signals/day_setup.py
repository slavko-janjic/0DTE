"""Pure, testable logic for the pre-market "day setup": the overnight gap, the
prior-day / overnight levels that become intraday support/resistance, today's
scheduled catalysts, and an opening-bias read. Like signals/indicators.py these
take plain numbers / dicts (never DataFrames or a DB handle) so the worker does
the I/O and these stay easy to unit test.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo


# --- hand-maintained calendar coverage ----------------------------------------
# market_catalysts and market_holidays/market_half_days are typed in by hand,
# a year or so at a time. When one runs out nothing notices: past the last
# catalyst the autopilot silently stops standing down around FOMC/CPI, and past
# the last holiday a closed market reads as a normal trading day. Each list
# therefore carries a *_through date - the last day it is known complete for.
# The last listed event can't stand in for it: a list complete through December
# may simply have nothing after the 10th.

CALENDAR_WARN_DAYS = 30

_CALENDARS = (
    # (config key, display name, the lists it vouches for)
    ("market_catalysts_through", "Catalyst calendar", "market_catalysts"),
    ("market_holidays_through", "Holiday calendar", "market_holidays / market_half_days"),
)


def calendar_coverage(config: dict, today: date,
                      warn_days: int = CALENDAR_WARN_DAYS) -> list[dict]:
    """The hand-maintained calendars that need attention, most urgent first:
    'expired' (today is past its *_through date - the autopilot stands down),
    'expiring' (within warn_days of it) or 'unset' (no *_through date, so
    nothing can tell whether it's current - warned about, but not blocking).
    Empty when every calendar is covered for more than warn_days.

    Each entry: {key, name, through (ISO or None), days_left, status, message}."""
    entries = []
    for key, name, lists in _CALENDARS:
        raw = config.get(key)
        if not raw:
            entries.append({
                "key": key, "name": name, "through": None, "days_left": None,
                "status": "unset",
                "message": f"{key} isn't set in settings.yaml, so nothing checks "
                           f"whether {lists} is still current.",
            })
            continue
        through = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
        days_left = (through - today).days
        fix = f"add the new dates to {lists} in settings.yaml and move {key}"
        if days_left < 0:
            status = "expired"
            message = (f"{name} ended {through.isoformat()} - the autopilot is standing "
                       f"down until you {fix}.")
        elif days_left < warn_days:
            status = "expiring"
            when = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
            message = (f"{name} ends {through.isoformat()} ({when}) - {fix}, or the "
                       f"autopilot stands down after that date.")
        else:
            continue
        entries.append({"key": key, "name": name, "through": through.isoformat(),
                        "days_left": days_left, "status": status, "message": message})
    order = {"expired": 0, "expiring": 1, "unset": 2}
    return sorted(entries, key=lambda entry: order[entry["status"]])


def calendar_blocker(config: dict, today: date) -> str | None:
    """Why the autopilot must stand down for calendar reasons, or None. Only an
    EXPIRED calendar blocks: trading on past it means holding through
    unlisted catalysts or treating a holiday as a session."""
    for entry in calendar_coverage(config, today):
        if entry["status"] == "expired":
            return (f"{entry['name'].lower()} ended {entry['through']} - extend it "
                    f"in settings.yaml")
    return None


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


# An index ETF does not gap 4000% overnight, and its overnight range does not sit
# 40x away from yesterday's close. Values like that are a data fault (a units
# mismatch once fed NASDAQ-100 index levels into a QQQ setup), and storing them
# poisons everything downstream - the day-setup card printed "+4076.43%" and the
# chart drew a 28,000 level on a 683 stock. Reject at the point of construction
# so nothing downstream has to defend itself.
MAX_PLAUSIBLE_GAP_PCT = 25.0
MAX_LEVEL_DEVIATION_PCT = 30.0


def plausible_gap(gap_percent: float | None,
                  max_pct: float = MAX_PLAUSIBLE_GAP_PCT) -> float | None:
    """The gap if it could physically be one, else None. NaN is rejected too."""
    if not isinstance(gap_percent, (int, float)) or isinstance(gap_percent, bool):
        return None
    if gap_percent != gap_percent:          # NaN
        return None
    return gap_percent if abs(gap_percent) <= max_pct else None


def plausible_level(level: float | None, reference: float | None,
                    max_deviation_pct: float = MAX_LEVEL_DEVIATION_PCT) -> float | None:
    """A price level if it sits within max_deviation_pct of a reference price
    (yesterday's close), else None - it is in the wrong units or simply wrong."""
    if not isinstance(level, (int, float)) or isinstance(level, bool):
        return None
    if level != level or level <= 0:
        return None
    if not isinstance(reference, (int, float)) or not reference or reference <= 0:
        return level                        # nothing to check against; pass through
    return level if abs(level - reference) / reference <= (max_deviation_pct / 100.0) else None


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
    overnight quote is unavailable).

    Implausible values are dropped rather than stored: a missing field reads
    honestly as "unknown", whereas a stored +4076% gap is presented to the user
    as fact and drawn on charts.
    """
    gap_percent = plausible_gap(gap_percent)
    overnight_high = plausible_level(overnight_high, prior_close)
    overnight_low = plausible_level(overnight_low, prior_close)
    prior_high = plausible_level(prior_high, prior_close)
    prior_low = plausible_level(prior_low, prior_close)

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
