"""session_lab: sessions from minute bars, the ten pre-declared signals, the
call/put label, and the per-period verdict."""
from datetime import date, datetime, timedelta, timezone

import pytest

from analytics import session_lab as lab

EXITS = {"profit_target_pct": 50, "stop_loss_pct": -35, "time_cutoff_minutes_before_close": 30,
         "reversal_confidence_pct": 101}


def _session(day: date, open_: float, at_decision: float, close: float | None = None,
             first15=None) -> lab.Session:
    """A full regular session: a straight line from open_ to at_decision over
    bars 0..29, then flat (or a line to `close`) for the rest of the day."""
    close = at_decision if close is None else close
    s = lab.Session(day)
    for m in range(390):
        if m < 30:
            price = open_ + (at_decision - open_) * (m + 1) / 30
        else:
            price = at_decision + (close - at_decision) * (m - 29) / 360
        o = open_ if m == 0 else s.bars[m - 1].close
        s.bars[m] = lab.Bar(o, max(o, price), min(o, price), price, 1000.0)
    if first15:
        hi, lo = first15
        for m in range(15):
            b = s.bars[m]
            s.bars[m] = lab.Bar(b.open, max(b.high, hi), min(b.low, lo), b.close, b.volume)
    return s


def _history(n: int, start: date = date(2024, 1, 2), drift: float = 0.5,
             gap: float = 0.0) -> list[lab.Session]:
    """n sessions whose first-30-minute move alternates +-drift (median |r30|
    known), each opening `gap` away from the last close (alternating sign)."""
    out, price, day = [], 100.0, start
    for i in range(n):
        move = drift if i % 2 == 0 else -drift
        open_ = price + (gap if i % 2 == 0 else -gap)
        out.append(_session(day, open_, open_ + move))
        price = open_ + move
        day += timedelta(days=1)
    return out


def test_sessions_from_bars_uses_market_time_across_dst():
    rows = []
    for day, offset in ((date(2024, 3, 8), 5), (date(2024, 3, 11), 4)):     # EST, then EDT
        base = datetime(day.year, day.month, day.day, 9 + offset, 30, tzinfo=timezone.utc)
        rows += [(base + timedelta(minutes=m), 1, 1, 1, 1.0 + m, 10) for m in range(390)]
        rows.append((base - timedelta(minutes=5), 1, 1, 1, 1, 10))            # pre-market: dropped
    sessions = lab.sessions_from_bars(rows)
    assert [s.day for s in sessions] == [date(2024, 3, 8), date(2024, 3, 11)]
    assert all(min(s.bars) == 0 and max(s.bars) == 389 for s in sessions)
    assert sessions[1].bars[0].close == 1.0          # 09:30 EDT is 13:30 UTC, not 14:30


def test_incomplete_and_half_days_are_not_complete():
    full = _session(date(2024, 1, 2), 100, 101)
    assert full.complete()
    half = lab.Session(date(2024, 11, 29), {m: b for m, b in full.bars.items() if m < 210})
    assert not half.complete()


def test_signals_wait_for_twenty_sessions_of_history():
    signals = lab.ticker_signals(_history(21))
    days = sorted(signals)
    assert signals[days[0]]["opening_drive"] is None          # no median yet
    assert signals[days[-1]]["opening_drive"] is not None


def test_opening_drive_scales_against_the_normal_move():
    history = _history(20, drift=0.5)                         # median |r30| ~0.5%
    today = _session(date(2024, 2, 1), history[-1].last_close(),
                     history[-1].last_close() * 1.005)        # +0.5%: about one normal move
    score = lab.ticker_signals(history + [today])[today.day]["opening_drive"]
    assert score == pytest.approx(0.5, abs=0.05)


def test_no_normal_gap_size_means_no_gap_signal():
    history = _history(20)                                    # never gapped
    today = _session(date(2024, 2, 1), history[-1].last_close() * 1.01,
                     history[-1].last_close() * 1.01)
    assert lab.ticker_signals(history + [today])[today.day]["gap_direction"] is None


def test_gap_direction_and_behaviour():
    history = _history(21, gap=0.2)       # 20 gaps (the first session has no prior close)
    prior = history[-1].last_close()
    # gapped up 1% at the open, then sold off in the first half hour (filling)
    today = _session(date(2024, 2, 1), prior * 1.01, prior * 1.004)
    scores = lab.ticker_signals(history + [today])[today.day]
    assert scores["gap_direction"] > 0
    assert scores["gap_behaviour"] == scores["opening_drive"] < 0


def test_orb15_breakout_above_below_and_inside():
    history = _history(20)
    base = history[-1].last_close()
    up = _session(date(2024, 2, 1), base, base + 1.0, first15=(base + 0.2, base - 0.2))
    inside = _session(date(2024, 2, 1), base, base, first15=(base + 0.5, base - 0.5))
    assert lab.ticker_signals(history + [up])[up.day]["orb15"] > 0
    assert lab.ticker_signals(history + [inside])[inside.day]["orb15"] == 0.0


def test_prior_levels():
    history = _history(20)
    prev = history[-1]
    above = _session(date(2024, 2, 1), prev.last_close(), prev.high() + 1)
    assert lab.ticker_signals(history + [above])[above.day]["prior_levels"] == 1.0


def test_vix_change_reads_only_days_before_today():
    history = _history(20)
    today = _session(date(2024, 2, 1), history[-1].last_close(), history[-1].last_close())
    vix = {date(2024, 1, 30): 15.0, date(2024, 1, 31): 16.5, date(2024, 2, 1): 30.0}
    score = lab.ticker_signals(history + [today], vix)[today.day]["vix_change"]
    assert score == pytest.approx(-1.0)        # +10% yesterday -> bearish; today's 30 unseen


def test_rel_strength_compares_the_two_tickers():
    spy, qqq = _history(20), _history(20)
    spy.append(_session(date(2024, 2, 1), spy[-1].last_close(), spy[-1].last_close()))
    qqq.append(_session(date(2024, 2, 1), qqq[-1].last_close(), qqq[-1].last_close() * 1.005))
    signals = lab.build_signals({"SPY": spy, "QQQ": qqq})
    assert signals[("QQQ", date(2024, 2, 1))]["rel_strength"] > 0
    assert signals[("SPY", date(2024, 2, 1))]["rel_strength"] < 0
    assert "_z" not in signals[("QQQ", date(2024, 2, 1))]


def test_label_prices_both_sides_from_the_decision():
    rally = _session(date(2024, 2, 1), 100.0, 100.0, close=101.5)
    label = lab.label_session(rally, 0.15, EXITS)
    assert label["call"] > 0 > label["put"]
    assert label["call_exit"] == "profit_target"


def _labels_for(days: list[date], call: float, put: float) -> dict:
    return {(t, d): {"call": call, "put": put} for d in days for t in ("SPY", "QQQ")}


def test_excess_is_measured_against_the_coin_flip_one_observation_per_day():
    day = date(2024, 2, 1)
    labels = _labels_for([day], call=40.0, put=-20.0)          # coin flip = +10
    signals = {("SPY", day): {"opening_drive": 0.6}, ("QQQ", day): {"opening_drive": -0.6}}
    out = lab.daily_outcomes(signals, labels, "opening_drive", 1)
    assert out[day]["excess"] == pytest.approx(0.0)            # (+30 - 30) / 2
    assert out[day]["trades"] == 2
    follow_both = {k: {"opening_drive": 0.6} for k in labels}
    assert lab.daily_outcomes(follow_both, labels, "opening_drive", 1)[day]["excess"] == 30.0
    assert lab.daily_outcomes(follow_both, labels, "opening_drive", -1)[day]["excess"] == -30.0


def test_verdict_needs_every_period_and_costs_covered():
    floor = lab.noise_floor(20)
    days = [date(y, 3, d) for y in (2017, 2020, 2023, 2025) for d in range(1, 29)]
    signals = {(t, d): {"opening_drive": 1.0} for d in days for t in ("SPY", "QQQ")}
    labels = {}
    for i, d in enumerate(days):
        wobble = 4.0 if i % 2 else -4.0
        for t in ("SPY", "QQQ"):
            labels[(t, d)] = {"call": 10.0 + wobble, "put": -20.0}
    result = lab.evaluate_test(signals, labels, "opening_drive", 1, floor)
    assert result["verdict"] == "PASS" and result["t"] > floor
    # same edge, but it flips sign in one period -> not a pass
    for d in days:
        if d.year == 2020:
            for t in ("SPY", "QQQ"):
                labels[(t, d)] = {"call": -20.0, "put": 10.0}
    assert lab.evaluate_test(signals, labels, "opening_drive", 1, floor)["verdict"] != "PASS"


def test_twenty_tests_and_their_noise_floor():
    assert len(lab.SIGNALS) * len(lab.ORIENTATIONS) == 20
    assert lab.noise_floor(20) == pytest.approx(2.45, abs=0.01)
