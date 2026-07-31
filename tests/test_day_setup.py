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


# --- implausible values are dropped, not stored ------------------------------
# From a real screenshot: QQQ prior close 683.55, gap "+4076.43%", overnight
# range "28,450.00 - 28,640.75" (NASDAQ-100 index levels on an ETF chart).

def test_plausible_gap_rejects_impossible_gaps():
    assert day_setup.plausible_gap(1.5) == 1.5
    assert day_setup.plausible_gap(-2.0) == -2.0
    assert day_setup.plausible_gap(4076.43) is None      # the screenshot value
    assert day_setup.plausible_gap(910.97) is None
    assert day_setup.plausible_gap(float("nan")) is None
    assert day_setup.plausible_gap(None) is None


def test_plausible_level_rejects_wrong_units():
    assert day_setup.plausible_level(690.0, 683.55) == 690.0     # ordinary level
    assert day_setup.plausible_level(28450.0, 683.55) is None    # index units
    assert day_setup.plausible_level(28640.75, 683.55) is None
    assert day_setup.plausible_level(0.0, 683.55) is None
    assert day_setup.plausible_level(None, 683.55) is None
    # no reference to check against -> pass the value through rather than guess
    assert day_setup.plausible_level(690.0, None) == 690.0


def test_build_day_setup_drops_the_screenshot_garbage():
    setup = day_setup.build_day_setup(
        "QQQ", gap_percent=4076.43, prior_high=692.3, prior_low=676.0,
        prior_close=683.55, overnight_high=28450.0, overnight_low=28640.75,
        catalysts_today=[],
    )
    assert setup["gap_pct"] is None
    assert setup["gap_direction"] is None
    assert setup["opening_bias"] is None          # no bias from a bogus gap
    assert setup["overnight_high"] is None
    assert setup["overnight_low"] is None
    # the good fields survive
    assert setup["prior_close"] == 683.55
    assert setup["prior_high"] == 692.3


def test_build_day_setup_keeps_a_genuinely_large_but_possible_gap():
    """A real 8% gap on an earnings night must NOT be filtered away."""
    setup = day_setup.build_day_setup(
        "NVDA", gap_percent=8.0, prior_high=210.0, prior_low=195.0,
        prior_close=200.0, overnight_high=216.0, overnight_low=199.0,
        catalysts_today=[],
    )
    assert setup["gap_pct"] == 8.0
    assert setup["gap_direction"] == "up"
    assert setup["overnight_high"] == 216.0
