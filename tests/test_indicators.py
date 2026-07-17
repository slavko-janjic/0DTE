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


def test_opening_range_score_bullish_on_breakout_above():
    # first 6 bars range 99-101, price now well above
    highs = [101] * 6 + [102] * 6
    lows = [99] * 6 + [101] * 6
    closes = [100] * 6 + [101.5] * 5 + [102.0]
    score = indicators.opening_range_score(closes, highs, lows)
    assert score is not None
    assert score > 0


def test_opening_range_score_bearish_on_breakdown_below():
    highs = [101] * 6 + [99] * 6
    lows = [99] * 6 + [97] * 6
    closes = [100] * 6 + [98.5] * 5 + [98.0]
    score = indicators.opening_range_score(closes, highs, lows)
    assert score is not None
    assert score < 0


def test_opening_range_score_zero_inside_range():
    highs = [101] * 12
    lows = [99] * 12
    closes = [100] * 12
    assert indicators.opening_range_score(closes, highs, lows) == 0.0


def test_opening_range_score_none_without_enough_bars():
    assert indicators.opening_range_score([100] * 4, [101] * 4, [99] * 4) is None


def test_compute_technicals_score_works_without_highs_lows():
    # backwards compatible: highs/lows optional
    closes = [100 + i * 0.1 for i in range(20)]
    volumes = [1000] * 20
    assert indicators.compute_technicals_score(closes, volumes, None, None) is not None


# --- IV skew: a 25-delta risk reversal, normalised by ATM IV -----------------
# The old version differenced the ATM call's IV against the ATM PUT's IV at the
# same strike, which put-call parity pins equal - it was scoring quote noise.

def test_iv_skew_score_neutral_at_the_baseline():
    # puts exactly 10% richer than calls relative to ATM IV = the normal state
    score = indicators.iv_skew_score(call_iv_otm=0.27, put_iv_otm=0.30, atm_iv=0.30,
                                     baseline_ratio=0.10, scale=0.10)
    assert score == pytest.approx(0.0)


def test_iv_skew_score_bearish_when_skew_steepens():
    # puts much richer than usual -> crash protection being bid -> fear
    score = indicators.iv_skew_score(call_iv_otm=0.24, put_iv_otm=0.30, atm_iv=0.30,
                                     baseline_ratio=0.10, scale=0.10)
    assert score < 0


def test_iv_skew_score_bullish_when_skew_flattens():
    # puts barely richer than calls -> complacency / upside chase
    score = indicators.iv_skew_score(call_iv_otm=0.30, put_iv_otm=0.30, atm_iv=0.30,
                                     baseline_ratio=0.10, scale=0.10)
    assert score > 0


def test_iv_skew_score_normalises_across_tickers():
    """The same RELATIVE skew on a low-vol name and a high-vol name must score
    the same - otherwise TSLA's IV points swamp SPY's."""
    low = indicators.iv_skew_score(0.09, 0.11, 0.10, 0.10, 0.10)   # 20% ratio
    high = indicators.iv_skew_score(0.72, 0.88, 0.80, 0.10, 0.10)  # 20% ratio
    assert low == pytest.approx(high)


def test_iv_skew_score_rejects_garbage_iv_rather_than_scoring_it():
    """yfinance returns 0.0 / 0.001 when its solver fails. Differencing those
    produced a confident score from nothing - the actual bug. No data must mean
    NO SIGNAL, so the composite renormalises around it."""
    assert indicators.iv_skew_score(0.001, 0.0, 0.30) is None      # solver failed
    assert indicators.iv_skew_score(0.27, 0.30, 0.0) is None       # dead ATM IV
    assert indicators.iv_skew_score(0.27, 9.9, 0.30) is None       # 990% IV
    assert indicators.iv_skew_score(None, 0.30, 0.30) is None
    assert indicators.iv_skew_score(float("nan"), 0.30, 0.30) is None


def test_valid_iv_bounds():
    assert indicators.valid_iv(0.30) is True
    assert indicators.valid_iv(0.0) is False       # yfinance's failure value
    assert indicators.valid_iv(0.001) is False     # 0.1% IV isn't real
    assert indicators.valid_iv(6.0) is False       # 600% IV isn't real
    assert indicators.valid_iv(None) is False


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


def test_interpolate_probability_above_exact_strike_match():
    ladder = [(100, 0.8), (110, 0.5), (120, 0.2)]
    assert indicators.interpolate_probability_above(ladder, spot=110) == 0.5


def test_interpolate_probability_above_between_strikes():
    ladder = [(100, 0.8), (110, 0.5), (120, 0.2)]
    # halfway between 100 and 110 -> halfway between 0.8 and 0.5
    assert indicators.interpolate_probability_above(ladder, spot=105) == pytest.approx(0.65)


def test_interpolate_probability_above_clamps_below_range():
    ladder = [(100, 0.8), (110, 0.5), (120, 0.2)]
    assert indicators.interpolate_probability_above(ladder, spot=50) == 0.8


def test_interpolate_probability_above_clamps_above_range():
    ladder = [(100, 0.8), (110, 0.5), (120, 0.2)]
    assert indicators.interpolate_probability_above(ladder, spot=200) == 0.2


def test_interpolate_probability_above_none_when_empty():
    assert indicators.interpolate_probability_above([], spot=100) is None


def test_prediction_market_score_maps_probability_to_directional_score():
    ladder = [(100, 0.9), (110, 0.1)]
    # spot exactly at the strike implying 90% chance of closing higher -> strongly bullish
    assert indicators.prediction_market_score(ladder, spot=100) == pytest.approx(0.8)
    # 10% chance of closing higher -> strongly bearish
    assert indicators.prediction_market_score(ladder, spot=110) == pytest.approx(-0.8)


def test_prediction_market_score_none_when_no_data():
    assert indicators.prediction_market_score([], spot=100) is None


def test_term_structure_score_bearish_on_backwardation():
    # VIX9D above VIX = near-term stress = bearish
    score = indicators.term_structure_score(vix9d=20, vix=16)
    assert score is not None
    assert score < 0


def test_term_structure_score_bullish_on_contango():
    # VIX9D below VIX = calm/normal = mildly bullish
    score = indicators.term_structure_score(vix9d=12, vix=16)
    assert score is not None
    assert score > 0


def test_term_structure_score_none_when_missing_data():
    assert indicators.term_structure_score(None, 16) is None
    assert indicators.term_structure_score(12, None) is None


def test_vvix_score_bearish_when_elevated():
    assert indicators.vvix_score(vvix=130) < 0


def test_vvix_score_bullish_when_low():
    assert indicators.vvix_score(vvix=60) > 0


def test_vvix_score_none_when_missing():
    assert indicators.vvix_score(None) is None


def test_compute_volatility_regime_score_combines_components():
    score = indicators.compute_volatility_regime_score(vix9d=12, vix=16, vvix=70)
    assert score is not None
    assert -1.0 <= score <= 1.0


def test_compute_volatility_regime_score_none_when_all_missing():
    assert indicators.compute_volatility_regime_score(None, None, None) is None


def test_trump_headline_score_bearish_on_tariff_heavy_headlines():
    headlines = [
        "Trump announces new tariffs on China",
        "Markets brace for tariff crisis as Trump threatens sanctions",
    ]
    score = indicators.trump_headline_score(headlines)
    assert score is not None
    assert score < 0


def test_trump_headline_score_bullish_on_deal_heavy_headlines():
    headlines = [
        "Trump announces trade deal and ceasefire agreement",
        "Stocks rally on stimulus optimism after Trump remarks",
    ]
    score = indicators.trump_headline_score(headlines)
    assert score is not None
    assert score > 0


def test_trump_headline_score_neutral_when_no_keyword_hits():
    headlines = ["Trump visits Ohio for campaign event"]
    assert indicators.trump_headline_score(headlines) == 0.0


def test_trump_headline_score_none_when_no_headlines():
    assert indicators.trump_headline_score(None) is None
    assert indicators.trump_headline_score([]) is None
