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


def _kalshi_book(spot_center=7500, width=25, n=8):
    """A realistic Kalshi book: `between` buckets around a centre, plus the two
    open-ended tails, with probabilities summing to ~1."""
    buckets = []
    lo = spot_center - (n // 2) * width
    for i in range(n):
        f = lo + i * width
        buckets.append({"floor": f, "cap": f + width, "prob": 1.0 / (n + 2)})
    buckets.append({"floor": lo + n * width, "cap": None, "prob": 1.0 / (n + 2)})  # top tail
    buckets.append({"floor": None, "cap": lo, "prob": 1.0 / (n + 2)})              # bottom tail
    return buckets


def test_probability_above_is_about_half_at_the_centre():
    book = _kalshi_book(spot_center=7500, width=25, n=8)
    # centre of the book -> roughly a coin flip, NOT -0.99
    p = indicators.probability_above(book, spot=7500)
    assert p is not None
    assert 0.35 < p < 0.65


def test_probability_above_high_when_spot_is_low():
    book = _kalshi_book(spot_center=7500, width=25, n=8)
    p = indicators.probability_above(book, spot=7405)  # near the bottom of the ladder
    assert p > 0.8


def test_probability_above_uses_the_whole_distribution_not_just_the_tail():
    """THE bug: the old code kept only the single 'greater' market (the far
    upper tail) and read its ~0.5% as P(above spot). That pinned the signal at
    -0.99 for its entire life while holding 15% of the composite on SPY/QQQ."""
    book = _kalshi_book(spot_center=7500, width=25, n=8)
    top_tail = [b for b in book if b["cap"] is None][0]
    assert top_tail["prob"] < 0.15                    # the tail alone is tiny...
    p = indicators.probability_above(book, spot=7500)
    assert p > 0.3                                    # ...but the real answer isn't


def test_probability_above_rejects_a_book_that_doesnt_sum_to_one():
    """After hours every bid is 0 and the whole book sums to ~0.3. Refuse rather
    than invent a probability from quotes nobody is standing behind."""
    thin = [{"floor": 7500, "cap": 7525, "prob": 0.005},
            {"floor": 7525, "cap": None, "prob": 0.005},
            {"floor": None, "cap": 7500, "prob": 0.005}]
    assert indicators.probability_above(thin, spot=7510) is None


def test_probability_above_rejects_spot_outside_the_ladder():
    book = _kalshi_book(spot_center=7500, width=25, n=8)
    # spot far above the top tail's floor -> nothing brackets it
    assert indicators.probability_above(book, spot=99999) is None
    assert indicators.probability_above(book, spot=1) is None


def test_probability_above_none_when_empty():
    assert indicators.probability_above([], spot=100) is None


def test_prediction_market_score_maps_probability_to_directional_score():
    book = _kalshi_book(spot_center=7500, width=25, n=8)
    score = indicators.prediction_market_score(book, spot=7500)
    assert score is not None
    assert abs(score) < 0.35        # a balanced book is near-neutral, not pinned
    # bullish when spot sits low in the distribution
    assert indicators.prediction_market_score(book, spot=7405) > 0.5



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
