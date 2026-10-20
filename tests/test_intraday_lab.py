"""intraday_lab: signals at later decision minutes (no look-ahead), labels at
those minutes, the event calendar, and slice verdicts."""
import copy
from datetime import date, timedelta

import pytest

from analytics import intraday_lab as il
from analytics import session_lab as lab

EXITS = {"profit_target_pct": 50, "stop_loss_pct": -35, "time_cutoff_minutes_before_close": 30,
         "reversal_confidence_pct": 101}


def _session(day: date, open_: float, path) -> lab.Session:
    s = lab.Session(day)
    prev = open_
    for m in range(390):
        close = path(m)
        s.bars[m] = lab.Bar(prev, max(prev, close) + 0.02, min(prev, close) - 0.02, close, 100.0)
        prev = close
    return s


def _history(n: int) -> list[lab.Session]:
    out, day = [], date(2024, 1, 2)
    for i in range(n):
        slope = [0.01, -0.008, 0.004, -0.012, 0.006][i % 5]
        out.append(_session(day, 100.0, lambda m, a=slope: 100.0 + a * m + 0.05 * ((m % 7) - 3)))
        day += timedelta(days=1)
    return out


def test_signals_at_a_minute_never_see_later_bars():
    sessions = _history(25)
    before = il.ticker_signals_at(sessions, 120)[sessions[-1].day]
    future = copy.deepcopy(sessions)
    for m in range(120, 390):                          # rewrite everything from 11:30 on
        b = future[-1].bars[m]
        future[-1].bars[m] = lab.Bar(b.open * 2, b.high * 2, b.low * 2, b.close * 2, b.volume * 9)
    after = il.ticker_signals_at(future, 120)[sessions[-1].day]
    assert before == after
    assert any(v is not None for v in before.values())


def test_trail30_is_sized_against_the_same_minute_on_earlier_days():
    sessions = _history(21)
    today = sessions[-1]
    scores = il.ticker_signals_at(sessions, 60)[today.day]
    trail = (today.bars[59].close - today.bars[29].close) / today.bars[29].close
    earlier = [(s.bars[59].close - s.bars[29].close) / s.bars[29].close for s in sessions[:-1]]
    med = lab.median_abs(earlier)
    assert scores["trail30"] == pytest.approx(max(-1, min(1, trail / (2 * med))))


def test_label_at_a_later_minute_buys_at_that_minutes_price():
    s = _session(date(2024, 2, 1), 100.0, lambda m: 100.0 if m < 119 else 101.5)
    later = lab.label_session(s, 0.15, EXITS, decision_minute=120)
    early = lab.label_session(s, 0.15, EXITS)                  # 10:00: before the jump
    assert early["call"] > 0                                    # the jump helps a 10:00 call
    assert later["call"] < early["call"]                        # bought after it, at 101.5


def test_event_calendar_expiry_month_end_and_earnings():
    days = [date(2022, 4, 1) + timedelta(days=i) for i in range(92)]
    days = [d for d in days if d.weekday() < 5 and d != date(2022, 4, 15)]   # Good Friday shut
    tags = il.event_tags(days, fomc={date(2022, 5, 4)}, cpi=set(), jobs=set(),
                         earnings=[(date(2022, 4, 28), "amc"), (date(2022, 5, 10), "bmo")])
    assert "opex" in tags[date(2022, 4, 14)]                    # 3rd Friday a holiday -> Thursday
    assert "opex" in tags[date(2022, 5, 20)] and "quad_witching" not in tags[date(2022, 5, 20)]
    assert {"opex", "quad_witching"} <= tags[date(2022, 6, 17)]
    assert "month_end" in tags[date(2022, 4, 29)]
    assert {"month_end", "quarter_end"} <= tags[date(2022, 6, 30)]
    assert "after_megacap_earnings" in tags[date(2022, 4, 29)]  # amc Thu -> Fri
    assert "after_megacap_earnings" in tags[date(2022, 5, 10)]  # bmo -> same day
    assert "fomc" in tags[date(2022, 5, 4)]
    assert il.day_slices(date(2022, 5, 4), tags) >= {"Wed", "fomc"}


def test_a_weekend_announcement_tags_the_next_session():
    days = [date(2020, 3, 12), date(2020, 3, 13), date(2020, 3, 16), date(2020, 3, 17)]
    tags = il.event_tags(days, fomc={date(2020, 3, 15)}, cpi=set(), jobs=set(), earnings=[])
    assert "fomc" in tags[date(2020, 3, 16)]           # Sunday emergency cut -> Monday
    assert "fomc" not in tags.get(date(2020, 3, 13), set())


def _days_with(excess_by_day: dict) -> tuple[dict, dict]:
    """signals + labels that produce exactly these per-day excesses when followed."""
    signals, labels = {}, {}
    for day, excess in excess_by_day.items():
        for t in ("SPY", "QQQ"):
            signals[(t, day)] = {"x": 1.0}
            labels[(t, day)] = {"call": excess, "put": -excess}
    return signals, labels


def test_a_slice_that_reverses_the_edge_is_flagged():
    start = date(2016, 1, 4)
    days = [start + timedelta(days=i) for i in range(0, 700) if (start + timedelta(days=i)).weekday() < 5]
    fomc = set(days[::12])
    excess = {d: (-6.0 if d in fomc else 4.0) + (1.0 if i % 2 else -1.0) for i, d in enumerate(days)}
    signals, labels = _days_with(excess)
    tags = il.event_tags(days, fomc=fomc, cpi=set(), jobs=set(), earnings=[])
    rows = {r["slice"]: r for r in il.slice_report(signals, labels, "x", 1, tags)}
    assert rows["fomc"]["verdict"] == "breaks" and rows["fomc"]["days"] >= 30
    assert rows["Tue"]["verdict"] == "holds"
    assert rows["cpi"]["verdict"] == "too few days"


def test_coverage_limits_a_slice_to_the_years_its_source_covers():
    days = [date(2016, 1, 4) + timedelta(days=i) for i in range(1500)]
    days = [d for d in days if d.weekday() < 5]
    signals, labels = _days_with({d: 1.0 for d in days})
    tags = il.event_tags(days, set(), set(), set(), earnings=[])
    covered = {"Mon": (date(2019, 1, 1), date(2019, 12, 31))}
    rows = {r["slice"]: r for r in il.slice_report(signals, labels, "x", 1, tags, covered)}
    assert rows["Mon"]["days"] == sum(1 for d in days if d.year == 2019 and d.weekday() == 0)


def test_thirty_two_main_tests():
    assert il.MAIN_TESTS == 32
    assert lab.noise_floor(il.MAIN_TESTS) == pytest.approx(2.63, abs=0.01)


def test_oos_summary_reads_in_one_line():
    import research
    line = research.oos_summary([
        {"name": "trail30", "time": "10:30", "days": 25, "excess": 1.84, "t": 1.2,
         "read": "holding up"},
        {"name": "gap_behaviour", "time": "10:00", "days": 2, "excess": None},
    ], "2026-10-21")
    assert line == ("Since 2026-10-21: trail30 10:30: +1.8%/day over 25d (t +1.2) - holding up; "
                    "gap_behaviour 10:00: 2d, too early")
