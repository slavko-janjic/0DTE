"""Historical replay: runs the bar-derived (technicals) side of the signal
pipeline over weeks of 5-minute bars and grades it with the exact accuracy
machinery the live worker uses - thousands of graded samples in seconds
instead of waiting weeks of live polling.

Honest scope: only signals computable from price/volume bars replay cleanly
(technicals: RSI, VWAP, momentum, opening-range). Sentiment, Kalshi, IV skew,
VIX and news have no free history, so replayed results validate the technicals
leg and the opening-range tactic's direction-picking - not the full composite.
The tactic simulation is also spot-based: it grades whether the direction call
was right, not option P&L (premium adds leverage, theta, and spread on top).

Pure functions over plain dicts/lists, like signals/indicators.py - the CLI
(replay.py at the repo root) does the fetching and printing.
"""
from collections import defaultdict
from datetime import datetime

from signals import indicators
from signals.composite import direction_from_score

# skip the first bars of each session: momentum/opening-range need ~7 bars
# (35 min) of history before the technicals score is meaningful
_MIN_BARS = 7


def bars_to_sessions(bars_df, tz_name: str = "America/New_York") -> dict:
    """Groups an intraday OHLCV DataFrame (tz-aware DatetimeIndex) into
    {session_date: [bar dicts]} in market-local time, preserving bar order.
    Bar dicts carry timestamp/close/volume/high/low."""
    sessions: dict = defaultdict(list)
    if bars_df is None or bars_df.empty:
        return {}
    local = bars_df.tz_convert(tz_name) if bars_df.index.tz is not None else bars_df
    for ts, row in local.iterrows():
        close = float(row["Close"])
        if close <= 0:
            continue
        sessions[ts.date()].append({
            "timestamp": ts.to_pydatetime(),
            "close": close,
            "volume": float(row.get("Volume", 0.0) or 0.0),
            "high": float(row.get("High", close)),
            "low": float(row.get("Low", close)),
        })
    return dict(sessions)


def replay_technicals(sessions: dict, min_bars: int = _MIN_BARS) -> list[dict]:
    """Replays the live worker's technicals computation bar by bar: at each bar,
    the score uses only that session's bars up to and including it (exactly the
    day-so-far window the worker sees). Returns snapshot dicts shaped like
    signal_snapshots history, gradeable by analytics.accuracy."""
    snapshots = []
    for session_date in sorted(sessions):
        bars = sessions[session_date]
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        for i in range(min_bars, len(bars)):
            score = indicators.compute_technicals_score(
                closes[: i + 1], volumes[: i + 1], highs[: i + 1], lows[: i + 1],
            )
            if score is None:
                continue
            snapshots.append({
                "timestamp": bars[i]["timestamp"],
                "direction": direction_from_score(score),
                "confidence": abs(score) * 100.0,
                "composite_score": score,
                "spot_price": bars[i]["close"],
                "subscores": {"technicals": score},
            })
    return snapshots


def session_period_accuracy(evaluated: list[dict], tz_name: str = "America/New_York") -> dict:
    """Hit rate bucketed by when in the session the call was made - morning
    (before 11:00), midday (11:00-14:00), afternoon (after 14:00), local time.
    Answers 'is the signal only good at certain times of day?'."""
    buckets = {"morning": [0, 0], "midday": [0, 0], "afternoon": [0, 0]}
    for snap in evaluated:
        if not snap.get("evaluated"):
            continue
        hour = snap["timestamp"].hour + snap["timestamp"].minute / 60.0
        key = "morning" if hour < 11 else ("midday" if hour < 14 else "afternoon")
        buckets[key][0] += 1
        buckets[key][1] += 1 if snap["hit"] else 0
    return {
        key: {"graded": total, "accuracy_pct": (hits / total * 100.0) if total else None}
        for key, (total, hits) in buckets.items()
    }


def opening_range_tactic_stats(
    sessions: dict,
    decision_minutes: int = 60,
    exit_bars_before_end: int = 6,
    min_confidence_pct: float = 0.0,
    open_time: str = "09:30",
) -> dict:
    """Simulates the day-session tactic on history: at ~decision_minutes after
    the open, take the technicals direction (skip the day if neutral or below
    min_confidence_pct) and hold until exit_bars_before_end bars before the
    session's last bar (~the time cutoff). A win = spot moved in the called
    direction. Spot-based (see module docstring)."""
    open_h, open_m = (int(p) for p in open_time.split(":"))
    open_minutes = open_h * 60 + open_m
    trades = wins = 0
    move_pcts: list[float] = []
    for session_date in sorted(sessions):
        bars = sessions[session_date]
        if len(bars) <= exit_bars_before_end + _MIN_BARS:
            continue
        decision_idx = next(
            (i for i, b in enumerate(bars)
             if (b["timestamp"].hour * 60 + b["timestamp"].minute) - open_minutes >= decision_minutes),
            None,
        )
        exit_idx = len(bars) - 1 - exit_bars_before_end
        if decision_idx is None or decision_idx < _MIN_BARS or decision_idx >= exit_idx:
            continue
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        score = indicators.compute_technicals_score(
            closes[: decision_idx + 1], volumes[: decision_idx + 1],
            highs[: decision_idx + 1], lows[: decision_idx + 1],
        )
        if score is None:
            continue
        direction = direction_from_score(score)
        if direction == "neutral" or abs(score) * 100.0 < min_confidence_pct:
            continue  # correctly standing down counts as no trade, not a loss
        entry_spot, exit_spot = closes[decision_idx], closes[exit_idx]
        move_pct = (exit_spot - entry_spot) / entry_spot * 100.0
        signed = move_pct if direction == "bullish" else -move_pct
        trades += 1
        wins += 1 if signed > 0 else 0
        move_pcts.append(signed)
    return {
        "sessions": len(sessions),
        "trades": trades,
        "wins": wins,
        "win_rate_pct": (wins / trades * 100.0) if trades else None,
        "avg_favorable_move_pct": (sum(move_pcts) / len(move_pcts)) if move_pcts else None,
    }
