"""Pure helpers for the price chart: which sessions exist, which window to
plot, and which reference levels are safe to draw.

The level filter exists because of a bug that recurred for two weeks. The chart
draws key levels (prior close/high/low, overnight range) on the price axis and
trusts whatever the database holds. A units bug stored NASDAQ-100 index levels
(~28,000) on a QQQ chart trading near 700, so Altair expanded the y-axis to fit
them and squashed the actual price line into a flat smear at the bottom.

Fixing the data source was necessary but not sufficient: a stale worker process
kept regenerating the bad values every morning, and each time the chart happily
rendered them. A chart that can be destroyed by one bad number in the database
will be destroyed again. So the chart now refuses to plot a level that sits
absurdly far from the price it is supposed to annotate - the axis stays readable
no matter what the data says.

Pure functions over plain values, like the rest of analytics/.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def session_dates(snapshots: list[dict], tz_name: str = "America/New_York") -> list[date]:
    """Trading dates that actually have snapshots, most recent first.

    Drives the session picker: the chart should offer the days that exist rather
    than assuming "today", which is empty every evening and all weekend.
    """
    tz = ZoneInfo(tz_name)
    seen = {
        snap["timestamp"].astimezone(tz).date()
        for snap in snapshots
        if snap.get("timestamp") is not None
    }
    return sorted(seen, reverse=True)


def latest_session_with_data(
    snapshots: list[dict], tz_name: str = "America/New_York",
) -> date | None:
    """The most recent day that has data - the honest default for the chart.

    Defaulting to the calendar "today" means an empty chart every evening,
    weekend and holiday, which reads as breakage rather than as "the market is
    closed". Falling back to the last session with data always shows something.
    """
    dates = session_dates(snapshots, tz_name)
    return dates[0] if dates else None


def session_window(
    session: date, open_time: str = "09:30", close_time: str = "16:00",
    pad_minutes: int = 15,
) -> tuple[datetime, datetime]:
    """(start, end) naive market-local bounds for one session's x-axis, padded a
    little either side so points on the open/close aren't clipped at the edge.

    Naive on purpose: Altair rejects zoneinfo-aware datetimes in a scale domain,
    and the chart data is converted to naive market-local time to match.
    """
    open_h, open_m = (int(p) for p in open_time.split(":"))
    close_h, close_m = (int(p) for p in close_time.split(":"))
    start = datetime.combine(session, time(open_h, open_m))
    end = datetime.combine(session, time(close_h, close_m))
    return start - timedelta(minutes=pad_minutes), end + timedelta(minutes=pad_minutes)


def visible_levels(
    levels: dict[str, float | None], price_low: float, price_high: float,
    max_deviation_pct: float = 15.0,
) -> dict[str, float]:
    """The subset of reference levels close enough to the plotted prices to draw.

    A level further than max_deviation_pct outside the visible price range is
    dropped rather than plotted: it is either a data bug (index-scale values on
    an ETF chart) or so far away that drawing it would compress the price line
    into uselessness. Either way the chart is more informative without it.

    Levels that are None, non-numeric, or non-positive are skipped too.
    """
    if price_low is None or price_high is None or price_low <= 0:
        return {}
    low, high = min(price_low, price_high), max(price_low, price_high)
    # Tolerance is a fraction of the price LEVEL, not of the visible span: an
    # intraday span can be near zero on a quiet session, and scaling off that
    # would reject perfectly ordinary levels like yesterday's close.
    tolerance = high * (max_deviation_pct / 100.0)

    keep = {}
    for name, value in levels.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if value != value or value <= 0:      # NaN or nonsense
            continue
        if (low - tolerance) <= value <= (high + tolerance):
            keep[name] = float(value)
    return keep
