import pytest

from signals import indicators


def test_rsi_uptrend_is_high():
    closes = [100 + i for i in range(20)]  # steady uptrend
    value = indicators.rsi(closes)
    assert value is not None
    assert value > 70


def test_rsi_score_maps_above_50_to_positive():
    assert indicators.rsi_score(70) > 0
    assert indicators.rsi_score(30) < 0
    assert indicators.rsi_score(None) is None


def test_vwap_weights_by_volume():
    closes = [100, 200]
    volumes = [1, 0]
    assert indicators.vwap(closes, volumes) == 100


def test_price_vs_vwap_score_clips_to_range():
    score = indicators.price_vs_vwap_score(price=110, vwap_value=100, scale_pct=0.5)
    assert score == 1.0  # 10% above vwap, way past the 0.5% scale -> clipped


def test_momentum_score_needs_enough_bars():
    assert indicators.momentum_score([1, 2, 3], lookback=6) is None


def test_compute_technicals_score_averages_available_signals():
    closes = [100 + i * 0.1 for i in range(20)]
    volumes = [1000] * 20
    score = indicators.compute_technicals_score(closes, volumes)
    assert score is not None
    assert -1.0 <= score <= 1.0


def test_iv_skew_score_bullish_when_calls_richer():
    score = indicators.iv_skew_score(call_iv_atm=0.30, put_iv_atm=0.25, scale=0.05)
    assert score == pytest.approx(1.0)


def test_iv_skew_score_none_when_missing_data():
    assert indicators.iv_skew_score(None, 0.25) is None


def test_call_put_volume_score_bullish_when_calls_dominate():
    score = indicators.call_put_volume_score(call_volume=800, put_volume=200)
    assert score == 0.6


def test_call_put_volume_score_none_when_no_volume():
    assert indicators.call_put_volume_score(0, 0) is None


def test_compute_max_pain_finds_min_payout_strike():
    strikes = [95, 100, 105]
    call_oi = [10, 10, 10]
    put_oi = [10, 10, 10]
    # symmetric OI -> the middle strike minimizes aggregate payout
    assert indicators.compute_max_pain(strikes, call_oi, put_oi) == 100


def test_max_pain_score_pulls_toward_higher_max_pain():
    score = indicators.max_pain_score(max_pain_strike=101, spot=100, scale_pct=1.0)
    assert score == 1.0


def test_compute_order_flow_score_combines_components():
    score = indicators.compute_order_flow_score(
        call_volume=700, put_volume=300, max_pain_strike=101, spot=100
    )
    assert score is not None
    assert -1.0 <= score <= 1.0
