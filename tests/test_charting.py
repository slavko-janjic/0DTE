"""Chart helpers: session selection and the defensive level filter.

The filter's job is to make the flat-chart bug structurally impossible. Fixing
the data source wasn't enough - a stale worker regenerated index-scale levels
every morning for two weeks and the chart rendered them every time.
"""
from datetime import date, datetime, timezone

import pytest

from analytics.charting import (
    latest_session_with_data, session_dates, session_window, visible_levels,
)


def _snap(day, hour=14):
    return {"timestamp": datetime(2026, 7, day, hour, 0, tzinfo=timezone.utc)}


# --- session selection -------------------------------------------------------

def test_session_dates_most_recent_first_and_deduped():
    snaps = [_snap(20), _snap(20, 15), _snap(21), _snap(17)]
    assert session_dates(snaps, "UTC") == [date(2026, 7, 21), date(2026, 7, 20),
                                           date(2026, 7, 17)]


def test_session_dates_uses_market_timezone():
    # 01:00 UTC on the 21st is still the evening of the 20th in New York
    snaps = [{"timestamp": datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)}]
    assert session_dates(snaps, "America/New_York") == [date(2026, 7, 20)]


def test_latest_session_falls_back_to_last_day_with_data():
    """The point of the feature: after hours / weekends, show the last real
    session instead of an empty 'today' that reads as breakage."""
    assert latest_session_with_data([_snap(17), _snap(20)], "UTC") == date(2026, 7, 20)
    assert latest_session_with_data([], "UTC") is None


def test_session_window_brackets_the_trading_day():
    start, end = session_window(date(2026, 7, 20), "09:30", "16:00", pad_minutes=15)
    assert (start.hour, start.minute) == (9, 15)
    assert (end.hour, end.minute) == (16, 15)
    assert start.tzinfo is None and end.tzinfo is None   # Altair needs naive


def test_session_window_respects_a_half_day_close():
    _, end = session_window(date(2026, 11, 27), "09:30", "13:00", pad_minutes=0)
    assert (end.hour, end.minute) == (13, 0)


# --- THE regression: the flat-chart bug --------------------------------------

def test_visible_levels_drops_the_index_scale_levels_that_flattened_qqq():
    """Real numbers from the bug: QQQ traded ~676-696 while the stored overnight
    range held NASDAQ-100 index values (~28,190). Altair expanded the y-axis to
    30,000 and the price line became a flat smear."""
    levels = {
        "prior_close": 682.1, "prior_high": 692.3, "prior_low": 676.0,
        "overnight_high": 28190.0, "overnight_low": 27839.5,
    }
    keep = visible_levels(levels, price_low=675.9, price_high=696.0)
    assert set(keep) == {"prior_close", "prior_high", "prior_low"}
    assert "overnight_high" not in keep and "overnight_low" not in keep


def test_visible_levels_keeps_ordinary_nearby_levels():
    levels = {"prior_close": 739.1, "prior_high": 745.5, "prior_low": 735.9}
    keep = visible_levels(levels, price_low=741.0, price_high=744.0)
    assert keep == levels


def test_visible_levels_keeps_levels_just_outside_the_visible_range():
    """Yesterday's high sitting a little above today's range is exactly the kind
    of level worth drawing - the filter must not be so tight it removes those."""
    keep = visible_levels({"prior_high": 720.0}, price_low=690.0, price_high=700.0)
    assert "prior_high" in keep


def test_visible_levels_tolerance_scales_with_price_not_span():
    """A quiet session has a near-zero span; tolerance keyed off the span would
    reject ordinary levels."""
    keep = visible_levels({"prior_close": 205.0}, price_low=204.0, price_high=204.05)
    assert "prior_close" in keep


def test_visible_levels_skips_none_nan_and_nonsense():
    levels = {"a": None, "b": float("nan"), "c": 0.0, "d": -5.0, "e": "700", "f": 700.0}
    keep = visible_levels(levels, price_low=695.0, price_high=705.0)
    assert keep == {"f": 700.0}


def test_visible_levels_empty_when_no_price_range():
    assert visible_levels({"a": 100.0}, price_low=None, price_high=None) == {}
    assert visible_levels({"a": 100.0}, price_low=0.0, price_high=0.0) == {}


def test_visible_levels_deviation_threshold_is_configurable():
    levels = {"far": 800.0}
    assert "far" not in visible_levels(levels, 690.0, 700.0, max_deviation_pct=5.0)
    assert "far" in visible_levels(levels, 690.0, 700.0, max_deviation_pct=20.0)
