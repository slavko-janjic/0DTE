"""The cost-of-trading analytics: playability and the intraday cost curve."""
import pytest

from analytics.spreads import (
    cheapest_windows, playability_ratio, spread_by_minute_bucket, spread_summary,
)


def _q(mso, spread, ticker="NVDA"):
    return {"ticker": ticker, "minutes_since_open": mso, "spread_pct": spread}


# --- playability -------------------------------------------------------------

def test_playability_nvda_style_cheap_spread_is_very_playable():
    # NVDA: a 0.44% move on a $207 spot at delta 0.49 vs a 0.5% spread
    r = playability_ratio(typical_move_pct=0.438, spot=207.4, delta=0.49,
                          premium=1.95, spread_pct=0.5)
    assert r > 20  # a typical move covers the toll many times over


def test_playability_spy_style_wide_spread_is_unplayable():
    # SPY: a tiny 0.108% move vs a 15.4% spread - the toll eats it
    r = playability_ratio(typical_move_pct=0.108, spot=620.0, delta=0.5,
                          premium=1.2, spread_pct=15.4)
    assert r < 3  # no achievable directional edge rescues this contract


def test_playability_scales_the_right_way():
    base = dict(typical_move_pct=0.4, spot=200.0, delta=0.5, premium=2.0)
    tight = playability_ratio(**base, spread_pct=0.5)
    wide = playability_ratio(**base, spread_pct=5.0)
    assert tight == pytest.approx(wide * 10)   # 10x the toll -> 1/10 the playability
    # a bigger typical move is proportionally more playable
    bigger = playability_ratio(**{**base, "typical_move_pct": 0.8}, spread_pct=0.5)
    assert bigger == pytest.approx(tight * 2)


def test_playability_none_on_missing_or_zero_inputs():
    assert playability_ratio(None, 200.0, 0.5, 2.0, 1.0) is None
    assert playability_ratio(0.4, 200.0, 0.5, 2.0, None) is None
    assert playability_ratio(0.4, 200.0, 0.5, 0.0, 1.0) is None   # no premium
    assert playability_ratio(0.4, 200.0, 0.0, 2.0, 1.0) is None   # no delta


# --- intraday cost curve -----------------------------------------------------

def test_spread_by_minute_bucket_groups_and_medians():
    rows = [_q(5, 10.0), _q(20, 20.0),          # 0-30m bucket -> median 15
            _q(35, 2.0), _q(50, 4.0), _q(59, 3.0)]  # 30-60m bucket -> median 3
    buckets = spread_by_minute_bucket(rows, bucket_minutes=30)
    assert [b["bucket_label"] for b in buckets] == ["0-30m", "30-60m"]
    assert buckets[0]["median_spread_pct"] == 15.0
    assert buckets[1]["median_spread_pct"] == 3.0
    assert buckets[1]["samples"] == 3


def test_spread_by_minute_bucket_uses_median_not_mean():
    # one absurd stale quote must not drag the bucket
    rows = [_q(5, 2.0), _q(6, 2.0), _q(7, 2.0), _q(8, 900.0)]
    buckets = spread_by_minute_bucket(rows, bucket_minutes=30)
    assert buckets[0]["median_spread_pct"] == 2.0


def test_spread_by_minute_bucket_skips_unusable_rows():
    rows = [_q(None, 5.0), _q(10, None), _q(-5, 5.0), _q(10, 4.0)]
    buckets = spread_by_minute_bucket(rows)
    assert len(buckets) == 1 and buckets[0]["samples"] == 1


def test_cheapest_windows_ranks_and_respects_min_samples():
    buckets = [
        {"bucket_start": 0, "bucket_label": "0-30m", "median_spread_pct": 12.0, "samples": 20},
        {"bucket_start": 30, "bucket_label": "30-60m", "median_spread_pct": 3.0, "samples": 20},
        {"bucket_start": 60, "bucket_label": "60-90m", "median_spread_pct": 1.0, "samples": 2},
        {"bucket_start": 90, "bucket_label": "90-120m", "median_spread_pct": 5.0, "samples": 20},
    ]
    best = cheapest_windows(buckets, top=2, min_samples=5)
    # the 1.0% bucket is cheapest but too thin to trust -> excluded
    assert [b["bucket_label"] for b in best] == ["30-60m", "90-120m"]


def test_spread_summary():
    s = spread_summary([_q(1, 5.0), _q(2, 1.0), _q(3, 9.0)])
    assert s["samples"] == 3
    assert s["median_pct"] == 5.0
    assert s["best_pct"] == 1.0
    assert s["worst_pct"] == 9.0
    assert spread_summary([])["median_pct"] is None
