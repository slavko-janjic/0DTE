from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analytics import accuracy, replay

_ET = ZoneInfo("America/New_York")


def _bars_df(days: list[list[float]], start_date=datetime(2026, 7, 6)) -> pd.DataFrame:
    """5-min bars from per-day close lists; volume constant, high/low = close."""
    rows, index = [], []
    day = start_date
    for closes in days:
        ts = day.replace(hour=9, minute=30, tzinfo=_ET)
        for close in closes:
            index.append(ts)
            rows.append({"Close": close, "Volume": 1000.0, "High": close + 0.1, "Low": close - 0.1})
            ts += timedelta(minutes=5)
        day += timedelta(days=1)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def _trend(start: float, step: float, n: int) -> list[float]:
    return [start + step * i for i in range(n)]


def test_bars_to_sessions_groups_by_day():
    df = _bars_df([_trend(100, 0.1, 10), _trend(101, 0.1, 10)])
    sessions = replay.bars_to_sessions(df)
    assert len(sessions) == 2
    for bars in sessions.values():
        assert len(bars) == 10
        assert bars[0]["timestamp"].hour == 9


def test_replay_technicals_uptrend_is_bullish_and_grades_well():
    # a steady uptrend: technicals should call bullish and be right
    df = _bars_df([_trend(100, 0.2, 40), _trend(108, 0.2, 40)])
    sessions = replay.bars_to_sessions(df)
    snapshots = replay.replay_technicals(sessions)
    assert len(snapshots) > 40  # two sessions minus warmup bars
    assert all(s["direction"] == "bullish" for s in snapshots)
    assert all(s["subscores"]["technicals"] > 0 for s in snapshots)

    evaluated = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    overall = accuracy.overall_accuracy_pct(evaluated)
    assert overall == 100.0  # price only ever goes up


def test_replay_skips_warmup_bars():
    df = _bars_df([_trend(100, 0.2, 40)])
    snapshots = replay.replay_technicals(replay.bars_to_sessions(df))
    # first graded bar is index _MIN_BARS -> 40 - 7 snapshots
    assert len(snapshots) == 40 - replay._MIN_BARS
    expected_first = datetime(2026, 7, 6, 9, 30, tzinfo=_ET) + timedelta(minutes=5 * replay._MIN_BARS)
    assert snapshots[0]["timestamp"] == expected_first


def test_session_period_accuracy_buckets():
    base = datetime(2026, 7, 6, tzinfo=_ET)
    evaluated = [
        {"timestamp": base.replace(hour=10), "evaluated": True, "hit": True},
        {"timestamp": base.replace(hour=12), "evaluated": True, "hit": False},
        {"timestamp": base.replace(hour=15), "evaluated": True, "hit": True},
        {"timestamp": base.replace(hour=15, minute=30), "evaluated": False, "hit": None},
    ]
    buckets = replay.session_period_accuracy(evaluated)
    assert buckets["morning"] == {"graded": 1, "accuracy_pct": 100.0}
    assert buckets["midday"] == {"graded": 1, "accuracy_pct": 0.0}
    assert buckets["afternoon"] == {"graded": 1, "accuracy_pct": 100.0}


def test_opening_range_tactic_wins_on_trend_days():
    # two clean uptrend days: decision at ~10:30 -> bullish -> price keeps rising
    df = _bars_df([_trend(100, 0.2, 78), _trend(108, 0.2, 78)])  # full 6.5h sessions
    sessions = replay.bars_to_sessions(df)
    stats = replay.opening_range_tactic_stats(sessions, decision_minutes=60)
    assert stats["sessions"] == 2
    assert stats["trades"] == 2
    assert stats["wins"] == 2
    assert stats["win_rate_pct"] == 100.0
    assert stats["avg_favorable_move_pct"] > 0


def test_opening_range_tactic_stands_down_when_neutral():
    # dead-flat day -> neutral technicals -> no trade taken, not a loss
    df = _bars_df([[100.0] * 78])
    stats = replay.opening_range_tactic_stats(replay.bars_to_sessions(df))
    assert stats["trades"] == 0
    assert stats["win_rate_pct"] is None


def test_empty_input():
    assert replay.bars_to_sessions(None) == {}
    assert replay.replay_technicals({}) == []
