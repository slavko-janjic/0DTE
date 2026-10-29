"""premarket_lab: the pre-registered premarket tests (2026-10-29)."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from analytics import option_sim, premarket_lab
from analytics import session_lab as lab
from data import history

ET = ZoneInfo("America/New_York")


def _session(day, price=100.0, late=None):
    s = lab.Session(day)
    for m in range(390):
        p = late if late is not None and m >= 20 else price   # known by 10:00
        s.bars[m] = lab.Bar(p, p, p, p, 100.0)
    return s


def _pre(start, end, high=None, low=None):
    """A premarket with prints every 10 min: start until 08:00, end after."""
    bars = {}
    for m in range(0, premarket_lab.PRE_END, 10):
        p = start if m < premarket_lab.DRIFT_FROM else end
        bars[m] = lab.Bar(p, p, p, p, 10.0)
    if high is not None:
        bars[5] = lab.Bar(high, high, high, high, 1.0)
    if low is not None:
        bars[15] = lab.Bar(low, low, low, low, 1.0)
    return bars


def test_premarket_hours_are_four_to_nine_thirty():
    ts = lambda h, m: datetime(2024, 2, 1, h, m, tzinfo=ET)
    assert history.premarket_hours(ts(4, 0)) and history.premarket_hours(ts(9, 29))
    assert not history.premarket_hours(ts(9, 30)) and not history.premarket_hours(ts(3, 59))


def test_bars_land_on_minutes_since_four():
    rows = [(datetime(2024, 2, 1, 8, 0, tzinfo=ET).astimezone(timezone.utc), 1, 2, 0.5, 1.5, 10),
            (datetime(2024, 2, 1, 9, 30, tzinfo=ET).astimezone(timezone.utc), 1, 1, 1, 1, 1)]
    pre = premarket_lab.premarket_from_bars(rows)
    assert list(pre[date(2024, 2, 1)]) == [240]          # 09:30 is the open, not premarket


def test_features_drift_from_eight_and_the_full_range():
    f = premarket_lab.features(_pre(100.0, 101.0, high=102.0, low=99.0))
    assert f["drift"] == pytest.approx(0.01)
    assert (f["high"], f["low"]) == (102.0, 99.0)


def test_signals_need_twenty_days_of_history_and_score_against_it():
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(25)]
    sessions = [_session(d) for d in days]
    # 0.1% drift and a 1% range every day; the last day drifts 0.4% with a 3% range
    pre = {d: _pre(100.0, 100.1, high=100.5, low=99.5) for d in days[:-1]}
    pre[days[-1]] = _pre(100.0, 100.4, high=101.5, low=98.5)
    out = premarket_lab.ticker_signals(sessions, pre)
    assert "pre_drift" not in out.get(days[5], {})        # no normal size yet
    last = out[days[-1]]
    assert last["pre_drift"] == pytest.approx(1.0)        # 0.4% / (2 x 0.1%) clipped
    assert last["range_ratio"] == pytest.approx(3.0, rel=1e-3)
    assert last["pre_levels"] == 0.0                      # 10:00 price inside the range


def test_pre_levels_reads_the_ten_oclock_price_against_the_range():
    day = date(2024, 2, 1)
    out = premarket_lab.ticker_signals([_session(day, price=100.0, late=101.0)],
                                       {day: _pre(100.0, 100.0, high=100.5, low=99.5)})
    assert out[day]["pre_levels"] > 0


def test_move_vs_implied_is_one_for_an_average_day():
    s = _session(date(2024, 2, 1))
    sigma = 0.2
    years = (premarket_lab.EXIT_MINUTE - 30) / option_sim._MINUTES_PER_YEAR
    implied = 100.0 * sigma * years ** 0.5 * (2 / 3.141592653589793) ** 0.5
    for m in range(premarket_lab.EXIT_MINUTE - 1, 390):
        s.bars[m] = lab.Bar(100 + implied, 100 + implied, 100 + implied, 100 + implied, 1)
    assert premarket_lab.move_vs_implied(s, sigma) == pytest.approx(1.0, rel=1e-6)


def test_range_verdict_needs_wide_days_to_move_more_in_every_period():
    ratios, outcomes = {}, {}
    for year in (2016, 2017, 2020, 2021, 2023, 2024, 2025):
        for i in range(10):
            d = date(year, 3, 1) + timedelta(days=i)
            wide = i % 2 == 0
            ratios[("SPY", d)] = 2.0 if wide else 0.5
            outcomes[("SPY", d)] = (1.3 if wide else 0.7) + 0.01 * i
    r = premarket_lab.evaluate_range(ratios, outcomes, 1.48)
    assert r["verdict"] == "PASS" and r["t"] > 1.48
    assert r["wide_since_2022"] > 1.0


def test_agreement_counts_same_sign_days():
    signals = {("SPY", 1): {"a": 0.5, "b": 0.2}, ("SPY", 2): {"a": -0.5, "b": 0.3},
               ("SPY", 3): {"a": 0.5}}
    assert premarket_lab.agreement(signals, "a", "b") == {"days": 2, "agree_pct": 50.0}
