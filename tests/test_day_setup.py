from datetime import date, datetime
from zoneinfo import ZoneInfo

from signals import day_setup

_ET = ZoneInfo("America/New_York")


def test_gap_pct_basic_and_missing():
    assert day_setup.gap_pct(101.0, 100.0) == 1.0
    assert day_setup.gap_pct(99.0, 100.0) == -1.0
    assert day_setup.gap_pct(None, 100.0) is None
    assert day_setup.gap_pct(101.0, None) is None
    assert day_setup.gap_pct(101.0, 0) is None


def test_opening_bias_score_sign_scale_clamp():
    assert day_setup.opening_bias_score(0.25, scale_pct=0.5) == 0.5
    assert day_setup.opening_bias_score(-0.25, scale_pct=0.5) == -0.5
    assert day_setup.opening_bias_score(5.0, scale_pct=0.5) == 1.0   # clamped
    assert day_setup.opening_bias_score(-5.0, scale_pct=0.5) == -1.0
    assert day_setup.opening_bias_score(None) is None


def test_catalysts_for_date_filters_by_date():
    cfg = [
        {"date": "2026-07-15", "time": "08:30", "label": "CPI", "impact": "high"},
        {"date": "2026-07-16", "time": "14:00", "label": "FOMC", "impact": "high"},
    ]
    todays = day_setup.catalysts_for_date(cfg, date(2026, 7, 15))
    assert len(todays) == 1
    assert todays[0]["label"] == "CPI"
    assert day_setup.catalysts_for_date(cfg, date(2026, 7, 17)) == []
    assert day_setup.catalysts_for_date(None, date(2026, 7, 15)) == []


def test_minutes_to_next_catalyst():
    cats = [{"time": "08:30", "label": "CPI", "impact": "high"}]
    now = datetime(2026, 7, 15, 8, 20, tzinfo=_ET)
    assert day_setup.minutes_to_next_catalyst(cats, now) == 10.0
    # already past -> none ahead
    later = datetime(2026, 7, 15, 9, 0, tzinfo=_ET)
    assert day_setup.minutes_to_next_catalyst(cats, later) is None
    # low-impact ignored
    low = [{"time": "08:30", "label": "misc", "impact": "low"}]
    assert day_setup.minutes_to_next_catalyst(low, now) is None
    assert day_setup.minutes_to_next_catalyst(None, now) is None


def test_minutes_to_next_catalyst_picks_soonest():
    cats = [
        {"time": "10:00", "label": "A", "impact": "high"},
        {"time": "08:45", "label": "B", "impact": "high"},
    ]
    now = datetime(2026, 7, 15, 8, 30, tzinfo=_ET)
    assert day_setup.minutes_to_next_catalyst(cats, now) == 15.0


def test_build_day_setup_full():
    cats = [{"time": "08:30", "label": "CPI", "impact": "high"}]
    setup = day_setup.build_day_setup(
        "SPY", gap_percent=0.4, prior_high=505.0, prior_low=500.0, prior_close=503.0,
        overnight_high=506.0, overnight_low=502.0, catalysts_today=cats, scale_pct=0.5,
    )
    assert setup["ticker"] == "SPY"
    assert setup["gap_direction"] == "up"
    assert setup["opening_bias"] == 0.8
    assert setup["prior_close"] == 503.0
    assert setup["catalysts"] == cats


def test_build_day_setup_partial_none_fields():
    setup = day_setup.build_day_setup(
        "IWM", gap_percent=None, prior_high=220.0, prior_low=218.0, prior_close=219.0,
        overnight_high=None, overnight_low=None, catalysts_today=None,
    )
    assert setup["gap_pct"] is None
    assert setup["gap_direction"] is None
    assert setup["opening_bias"] is None
    assert setup["prior_high"] == 220.0
    assert setup["catalysts"] == []


def test_build_day_setup_flat_gap():
    setup = day_setup.build_day_setup(
        "QQQ", gap_percent=0.01, prior_high=1, prior_low=1, prior_close=1,
        overnight_high=1, overnight_low=1, catalysts_today=[],
    )
    assert setup["gap_direction"] == "flat"
