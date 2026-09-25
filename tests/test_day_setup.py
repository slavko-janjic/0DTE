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


# --- calendar coverage -------------------------------------------------------------

_COVERED = {"market_catalysts_through": "2026-12-31", "market_holidays_through": "2028-12-31"}


def test_calendars_covered_well_ahead_need_no_attention():
    assert day_setup.calendar_coverage(_COVERED, date(2026, 9, 25)) == []
    assert day_setup.calendar_blocker(_COVERED, date(2026, 9, 25)) is None


def test_calendar_warns_inside_the_last_30_days_without_blocking():
    [entry] = day_setup.calendar_coverage(_COVERED, date(2026, 12, 10))
    assert (entry["key"], entry["status"], entry["days_left"]) == \
        ("market_catalysts_through", "expiring", 21)
    assert "2026-12-31" in entry["message"] and "in 21 days" in entry["message"]
    assert day_setup.calendar_blocker(_COVERED, date(2026, 12, 10)) is None
    # its last covered day still counts as covered
    [last] = day_setup.calendar_coverage(_COVERED, date(2026, 12, 31))
    assert last["status"] == "expiring" and "(today)" in last["message"]
    assert day_setup.calendar_blocker(_COVERED, date(2026, 12, 31)) is None


def test_expired_calendar_blocks_the_autopilot():
    entries = day_setup.calendar_coverage(_COVERED, date(2027, 1, 4))
    assert entries[0]["status"] == "expired"
    assert "standing down" in entries[0]["message"]
    blocker = day_setup.calendar_blocker(_COVERED, date(2027, 1, 4))
    assert blocker == "catalyst calendar ended 2026-12-31 - extend it in settings.yaml"


def test_expired_calendars_sort_first():
    config = {"market_catalysts_through": "2027-01-20", "market_holidays_through": "2026-12-31"}
    entries = day_setup.calendar_coverage(config, date(2027, 1, 4))
    assert [(e["key"], e["status"]) for e in entries] == [
        ("market_holidays_through", "expired"), ("market_catalysts_through", "expiring")]
    assert day_setup.calendar_blocker(config, date(2027, 1, 4)).startswith("holiday calendar")


def test_unset_coverage_is_flagged_but_never_blocks():
    entries = day_setup.calendar_coverage({}, date(2026, 9, 25))
    assert {e["status"] for e in entries} == {"unset"}
    assert day_setup.calendar_blocker({}, date(2026, 9, 25)) is None


def test_coverage_accepts_yaml_dates():
    # an unquoted YAML date parses to datetime.date, not a string
    config = {"market_catalysts_through": date(2026, 12, 31),
              "market_holidays_through": date(2028, 12, 31)}
    assert day_setup.calendar_blocker(config, date(2027, 1, 4)) is not None


def test_shipped_calendar_lists_stay_inside_their_coverage():
    # every listed holiday / early close is within market_holidays_through, and
    # the coverage keys parse (catalysts may list known dates past theirs, e.g.
    # 2027 FOMC, because the list is only COMPLETE through the key)
    from config import load_settings
    config = load_settings()
    holidays_through = date.fromisoformat(config["market_holidays_through"])
    date.fromisoformat(config["market_catalysts_through"])
    for day in config["market_holidays"] + config["market_half_days"]:
        assert date.fromisoformat(day) <= holidays_through
