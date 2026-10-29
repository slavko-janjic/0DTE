"""signals/morning.py: the pre-registered morning signals, computed live from
15-minute bars, must give exactly what the research lab computed from 1-minute
bars - otherwise the lab would forward-test something that was never tested."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analytics import session_lab as lab
from signals import morning

ET = ZoneInfo("America/New_York")


def _minute_session(day: date, open_: float, path) -> lab.Session:
    """A full session of 1-minute bars; path(m) gives the close of bar m."""
    s = lab.Session(day)
    prev = open_
    for m in range(390):
        close = path(m)
        s.bars[m] = lab.Bar(prev, max(prev, close) + 0.03, min(prev, close) - 0.03, close, 100.0)
        prev = close
    return s


def _history(n: int) -> list[lab.Session]:
    """Sessions with varied gaps and opening moves (some gap a lot, some fill)."""
    out, price, day = [], 100.0, date(2024, 1, 2)
    for i in range(n):
        gap = [0.15, -0.4, 0.05, 0.9, -0.1, -1.2, 0.3][i % 7]
        drift = [0.4, -0.3, 0.1, -0.6, 0.5, 0.2, -0.15][i % 5]
        open_ = price + gap
        session = _minute_session(day, open_, lambda m, o=open_, d=drift:
                                  o + d * min(m + 1, 30) / 30 + 0.2 * ((m % 11) - 5) / 5)
        out.append(session)
        price = session.last_close()
        day += timedelta(days=1)
    return out


def _to_15m(sessions: list[lab.Session], cut_today_at: int | None = None) -> pd.DataFrame:
    """Aggregate 1-minute sessions to the 15-minute frame Yahoo would return."""
    rows, index = [], []
    for k, s in enumerate(sessions):
        last = max(s.bars) if (cut_today_at is None or k < len(sessions) - 1) else cut_today_at
        for start in range(0, last + 1, 15):
            chunk = [s.bars[m] for m in range(start, min(start + 15, last + 1))]
            rows.append({"Open": chunk[0].open, "High": max(b.high for b in chunk),
                         "Low": min(b.low for b in chunk), "Close": chunk[-1].close})
            index.append(datetime(s.day.year, s.day.month, s.day.day, 9, 30, tzinfo=ET)
                         + timedelta(minutes=start))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def test_15_minute_bars_reproduce_the_research_scores_exactly():
    sessions = _history(26)
    today = sessions[-1].day
    research = lab.ticker_signals(sessions)[today]
    # at 10:00 today only the morning exists - the live worker never sees the rest
    live = morning.morning_scores(morning.sessions_from_15m(_to_15m(sessions, cut_today_at=29), ET),
                                  today)
    for name in morning.SIGNALS_1000:
        assert research[name] is not None or live[name] is None
        assert live[name] == pytest.approx(research[name]) if research[name] is not None \
            else live[name] is None
    assert any(live[name] is not None for name in morning.SIGNALS_1000)


def test_every_day_matches_not_just_the_last():
    sessions = _history(30)
    research = lab.ticker_signals(sessions)
    frame = _to_15m(sessions)
    parsed = morning.sessions_from_15m(frame, ET)
    for s in sessions[21:]:
        live = morning.morning_scores(parsed, s.day)
        for name in morning.SIGNALS_1000:
            expected = research[s.day][name]
            assert (live[name] is None) == (expected is None)
            if expected is not None:
                assert live[name] == pytest.approx(expected)


def test_the_1030_pair_matches_the_intraday_research_every_day():
    from analytics import intraday_lab
    sessions = _history(30)
    research = intraday_lab.ticker_signals_at(sessions, 60)
    parsed = morning.sessions_from_15m(_to_15m(sessions), ET)
    for s in sessions[22:]:
        live = morning.morning_scores(parsed, s.day)
        for live_name, research_name in (("trail30_1030", "trail30"), ("orb15_1030", "orb15")):
            expected = research[s.day][research_name]
            assert (live[live_name] is None) == (expected is None)
            if expected is not None:
                assert live[live_name] == pytest.approx(expected)


def test_the_1030_pair_waits_for_the_1015_bar():
    sessions = _history(22)
    live = morning.morning_scores(morning.sessions_from_15m(_to_15m(sessions, cut_today_at=44), ET),
                                  sessions[-1].day)
    assert live["gap_behaviour"] is not None or live["orb15"] is not None
    assert live["trail30_1030"] is None and live["orb15_1030"] is None


def test_nothing_before_the_0945_bar_has_closed():
    sessions = _history(22)
    frame = _to_15m(sessions, cut_today_at=14)          # 09:44: only the first bar today
    assert morning.morning_scores(morning.sessions_from_15m(frame, ET), sessions[-1].day) is None


def test_half_days_are_a_previous_close_but_not_a_normal_size():
    sessions = _history(23)
    half = sessions[-2]
    half.bars = {m: b for m, b in half.bars.items() if m < 210}
    research = lab.ticker_signals(sessions)[sessions[-1].day]
    live = morning.morning_scores(morning.sessions_from_15m(_to_15m(sessions), ET), sessions[-1].day)
    for name in morning.SIGNALS_1000:
        assert (live[name] is None) == (research[name] is None)
        if research[name] is not None:
            assert live[name] == pytest.approx(research[name])


def test_confirmed_trades_only_when_both_ten_thirty_signals_agree():
    assert morning.confirmed_score(0.6, 0.3) == 0.6
    assert morning.confirmed_score(-0.6, -0.1) == -0.6
    assert morning.confirmed_score(0.6, -0.3) == 0.0          # they disagree
    assert morning.confirmed_score(0.6, 0.0) == 0.0           # still inside the range
    assert morning.confirmed_score(None, 0.3) is None


def test_confirmed_stays_out_of_the_morning_family():
    from webapi import payloads
    strategies = [{"name": "a", "entry": {"signal_source": "trail30_1030"}},
                  {"name": "b", "entry": {"signal_source": "confirmed_1030"}}]
    assert payloads.morning_family(strategies) == ["a"]
