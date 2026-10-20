"""The pre-registered morning signals, computed live at 10:00 ET.

The signal research lab (analytics/session_lab.py, SIGNAL_RESEARCH.md) tested
ten pre-declared signals on 2016-2026 minute bars against the autopilot's
10:00 trade. `gap_behaviour` (follow) was the one pass; `orb15` (follow) the
runner-up. This module computes exactly those two, live, so the shadow lab can
forward-test them - with the SAME scoring functions the research used
(session_lab.gap_behaviour_score / orb15_score), so the definitions can't
drift apart.

Both need only: today's 09:30 open, the 09:30-09:44 high/low, the 09:59 close,
and the previous session's close - plus 20 earlier sessions for the "normal
size" medians. Yahoo's 15-minute bars carry all of that exactly (the 09:30 bar's
open/high/low, the 09:45 bar's close, the 15:45 bar's close) and go back ~60
days, so no minute-level history is needed.

Pure functions; the worker does the fetching.
"""
from dataclasses import dataclass
from datetime import date

from analytics import session_lab

SIGNALS = ("gap_behaviour", "orb15")
_FULL_DAY_BARS = 20             # of 26 fifteen-minute bars; a half-day has 14


@dataclass
class MorningSession:
    day: date
    open: float                 # 09:30 open
    high15: float               # 09:30-09:44 high
    low15: float                # 09:30-09:44 low
    price: float | None         # 09:59 close (None until the 09:45 bar exists)
    close: float                # the session's last close so far
    complete: bool              # a full regular session (not today, not a half-day)


def sessions_from_15m(bars, tz) -> list[MorningSession]:
    """Groups a 15-minute OHLC frame (tz-aware index) into sessions in market
    time, oldest first. A session without its 09:30 bar is dropped."""
    if bars is None or len(bars) == 0:
        return []
    local = bars.tz_convert(tz) if bars.index.tz is not None else bars
    by_day: dict = {}
    for ts, row in local.iterrows():
        minute = ts.hour * 60 + ts.minute - (9 * 60 + 30)
        if 0 <= minute < 390:
            by_day.setdefault(ts.date(), {})[minute] = row
    out = []
    for day in sorted(by_day):
        rows = by_day[day]
        first = rows.get(0)
        if first is None:
            continue
        second = rows.get(15)
        last = rows[max(rows)]
        out.append(MorningSession(
            day=day, open=float(first["Open"]), high15=float(first["High"]),
            low15=float(first["Low"]),
            price=float(second["Close"]) if second is not None else None,
            close=float(last["Close"]),
            complete=len(rows) >= _FULL_DAY_BARS and max(rows) >= 375))
    return out


def morning_scores(sessions: list[MorningSession], today: date) -> dict | None:
    """{'gap_behaviour': score or None, 'orb15': score or None} for `today`,
    using only sessions before it for the medians and the previous close -
    the same bookkeeping as session_lab.ticker_signals (complete sessions feed
    the medians; any session, even a half-day, is 'the previous close').
    None when today's 09:59 price isn't known yet."""
    current = next((s for s in sessions if s.day == today), None)
    if current is None or current.price is None:
        return None
    r30_hist, gap_hist, previous = [], [], None
    for session in (s for s in sessions if s.day < today):
        if session.complete:
            r30_hist.append((session.price - session.open) / session.open
                            if session.price is not None else None)
            if previous is not None:
                gap_hist.append((session.open - previous.close) / previous.close)
        previous = session
    r30_hist = [r for r in r30_hist if r is not None]

    r30 = (current.price - current.open) / current.open
    gap = (current.open - previous.close) / previous.close if previous else None
    return {
        "gap_behaviour": session_lab.gap_behaviour_score(
            r30, gap, session_lab.median_abs(r30_hist), session_lab.median_abs(gap_hist)),
        "orb15": session_lab.orb15_score(current.price, current.high15, current.low15),
    }
