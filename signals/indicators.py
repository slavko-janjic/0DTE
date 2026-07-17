"""Pure, testable functions that turn raw market data into -1..1 (bearish..bullish)
subscores for each signal category. Deliberately take plain lists/numbers rather
than DataFrames so they're easy to unit test with mocked inputs - the worker loop
is responsible for extracting these primitives from the data/sentiment layers.

All normalization scales below are arbitrary starting points per the project
brief - expect to tune them once real paper-trading data accumulates.
"""
import math


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _num(value) -> float:
    """Coerce a cell to a finite float, treating None/NaN/garbage as 0.0. The
    `x or 0.0` idiom is unsafe here because NaN is truthy (and yfinance leaves
    NaN in gamma / openInterest cells on illiquid strikes)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(value) else value


# --- technicals --------------------------------------------------------

def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_score(rsi_value: float | None) -> float | None:
    """Trend-following interpretation: RSI above 50 = bullish momentum."""
    if rsi_value is None:
        return None
    return _clip((rsi_value - 50.0) / 50.0)


def vwap(closes: list[float], volumes: list[float]) -> float | None:
    if not closes or not volumes or len(closes) != len(volumes):
        return None
    total_volume = sum(volumes)
    if total_volume == 0:
        return None
    return sum(c * v for c, v in zip(closes, volumes)) / total_volume


def price_vs_vwap_score(price: float, vwap_value: float | None, scale_pct: float = 0.5) -> float | None:
    if vwap_value is None or vwap_value == 0:
        return None
    pct_diff = (price - vwap_value) / vwap_value * 100.0
    return _clip(pct_diff / scale_pct)


def momentum_score(closes: list[float], lookback: int = 6, scale_pct: float = 0.3) -> float | None:
    if len(closes) <= lookback:
        return None
    start, end = closes[-lookback - 1], closes[-1]
    if start == 0:
        return None
    pct_change = (end - start) / start * 100.0
    return _clip(pct_change / scale_pct)


def opening_range_score(
    closes: list[float], highs: list[float], lows: list[float],
    opening_bars: int = 6, scale_pct: float = 0.2,
) -> float | None:
    """Opening-range breakout, a classic 0DTE read: the first `opening_bars`
    bars (6 x 5min = the first 30 minutes) define a high/low range. Price
    breaking above that range is bullish, below is bearish, inside is 0.
    Magnitude scales with how far past the range price has pushed."""
    if len(closes) <= opening_bars or len(highs) < opening_bars or len(lows) < opening_bars:
        return None
    range_high = max(highs[:opening_bars])
    range_low = min(lows[:opening_bars])
    if range_high <= 0:
        return None
    price = closes[-1]
    if price > range_high:
        return _clip((price - range_high) / range_high * 100.0 / scale_pct)
    if price < range_low:
        return _clip((price - range_low) / range_low * 100.0 / scale_pct)
    return 0.0


def compute_technicals_score(
    closes: list[float], volumes: list[float],
    highs: list[float] | None = None, lows: list[float] | None = None,
) -> float | None:
    scores = [
        s for s in (
            price_vs_vwap_score(closes[-1], vwap(closes, volumes)) if closes else None,
            momentum_score(closes),
            rsi_score(rsi(closes)),
            opening_range_score(closes, highs, lows) if highs and lows else None,
        ) if s is not None
    ]
    return sum(scores) / len(scores) if scores else None


# --- greeks / IV ---------------------------------------------------------
# This signal previously compared the ATM call's IV to the ATM PUT's IV at the
# SAME strike. That is provably meaningless: put-call parity is an arbitrage
# relation, so a call and put at one strike/expiry must carry the same IV - any
# difference is quote noise. Dividing that noise by scale=0.05 amplified it 20x,
# handed it 15% of the composite's weight, and left it correlated -0.68 with
# technicals - actively cancelling the one semi-useful signal we had.
#
# Real skew lives ACROSS strikes: what do traders pay for downside protection
# vs upside? That's the 25-delta risk reversal below.


def valid_iv(iv: float | None, lo: float = 0.01, hi: float = 5.0) -> bool:
    """Is this a believable implied volatility? Real option IV runs ~15-80%;
    yfinance regularly returns 0.001 (or exactly 0.0) when its solver fails or
    quotes are stale, and differencing two of those produces confident garbage.
    Better to emit no signal than a noise signal - the composite renormalises
    around a missing category."""
    return iv is not None and not math.isnan(iv) and lo <= iv <= hi


def iv_skew_score(
    call_iv_otm: float | None, put_iv_otm: float | None, atm_iv: float | None,
    baseline_ratio: float = 0.10, scale: float = 0.10,
) -> float | None:
    """25-delta risk reversal, normalised by ATM IV.

    Equity skew is persistently put-heavy - crash protection is always bid - so
    the LEVEL of skew isn't directional; a raw put_iv - call_iv would read
    permanently bearish. What carries information is whether skew is steeper or
    flatter than its usual state: steepening = fear being bid = bearish lean,
    flattening = complacency / upside chase = bullish lean.

    Normalising by ATM IV makes the number comparable across tickers (TSLA's
    skew and SPY's aren't on the same scale in absolute IV points).

    baseline_ratio and scale are arbitrary starting points per the project brief
    - they say "puts are typically ~10% richer than calls relative to ATM IV".
    Returns None unless all three IVs are believable.
    """
    if not (valid_iv(call_iv_otm) and valid_iv(put_iv_otm) and valid_iv(atm_iv)):
        return None
    ratio = (put_iv_otm - call_iv_otm) / atm_iv   # >0 = the normal put skew
    excess = ratio - baseline_ratio               # steeper than usual = fear
    return _clip(-excess / scale)


def compute_greeks_iv_score(
    call_iv_otm: float | None, put_iv_otm: float | None, atm_iv: float | None,
    baseline_ratio: float = 0.10, scale: float = 0.10,
) -> float | None:
    return iv_skew_score(call_iv_otm, put_iv_otm, atm_iv, baseline_ratio, scale)


# --- order flow ------------------------------------------------------------

def call_put_volume_score(call_volume: float, put_volume: float) -> float | None:
    total = call_volume + put_volume
    if total == 0:
        return None
    return _clip((call_volume - put_volume) / total)


def compute_max_pain(strikes: list[float], call_oi: list[float], put_oi: list[float]) -> float | None:
    """Strike at which option writers' aggregate payout is minimized."""
    if not strikes or len(strikes) != len(call_oi) or len(strikes) != len(put_oi):
        return None
    best_strike, best_loss = None, None
    for candidate in strikes:
        loss = 0.0
        for strike, c_oi, p_oi in zip(strikes, call_oi, put_oi):
            if candidate > strike:
                loss += (candidate - strike) * c_oi
            if candidate < strike:
                loss += (strike - candidate) * p_oi
        if best_loss is None or loss < best_loss:
            best_loss, best_strike = loss, candidate
    return best_strike


def max_pain_score(max_pain_strike: float | None, spot: float, scale_pct: float = 1.0) -> float | None:
    """Price is theorized to gravitate toward max pain by expiration."""
    if max_pain_strike is None or spot == 0:
        return None
    pct_diff = (max_pain_strike - spot) / spot * 100.0
    return _clip(pct_diff / scale_pct)


def compute_order_flow_score(
    call_volume: float, put_volume: float,
    max_pain_strike: float | None, spot: float,
) -> float | None:
    scores = [
        s for s in (
            call_put_volume_score(call_volume, put_volume),
            max_pain_score(max_pain_strike, spot),
        ) if s is not None
    ]
    return sum(scores) / len(scores) if scores else None


# --- dealer gamma exposure (GEX) --------------------------------------------
# Not a directional signal - gamma doesn't say up/down. It says whether the day
# is likely rangebound or trending, so it GATES the tactic rather than feeding
# the composite. Naive/front-expiry approximation (retail convention): dealers
# assumed long calls, short puts. Sum gamma*OI on each side; net-positive (calls
# dominate) => dealers long gamma => they fade moves => rangebound/pinning;
# net-negative (puts dominate) => short gamma => they chase => trending/amplified.
# OI is EOD-ish and gamma is BS-derived, so treat this as a regime hint, not a
# precise dollar figure.

def _gamma_oi_sum(gammas: list[float], open_interest: list[float]) -> float:
    return sum(_num(g) * _num(oi) for g, oi in zip(gammas, open_interest))


def gamma_exposure_score(
    call_gammas: list[float], call_oi: list[float],
    put_gammas: list[float], put_oi: list[float],
) -> float | None:
    """Scale-invariant net dealer gamma in -1..1: (call_gamma_oi - put_gamma_oi)
    / (call_gamma_oi + put_gamma_oi). Positive = call/long-gamma dominated
    (rangebound), negative = put/short-gamma dominated (trending). Works across
    tickers of very different notional (QQQ vs TSLA). None when there's no gamma
    on either side."""
    call_side = _gamma_oi_sum(call_gammas, call_oi)
    put_side = _gamma_oi_sum(put_gammas, put_oi)
    total = call_side + put_side
    if total <= 0:
        return None
    return _clip((call_side - put_side) / total)


def gamma_notional(
    call_gammas: list[float], call_oi: list[float],
    put_gammas: list[float], put_oi: list[float], spot: float,
) -> float | None:
    """Naive dollar GEX: net dealer gamma * spot^2 * 100 * 0.01 - the approx
    dollar hedging flow per 1% move. Sign matches gamma_exposure_score. For
    display; the score above is what drives the regime call. None if spot<=0."""
    if spot <= 0:
        return None
    net = _gamma_oi_sum(call_gammas, call_oi) - _gamma_oi_sum(put_gammas, put_oi)
    return net * spot * spot * 100.0 * 0.01


def gamma_regime(score: float | None, deadband: float = 0.15) -> str | None:
    """Classifies the normalized GEX score into a regime. Within +/-deadband is
    'neutral' (no strong lean). None passes through as None (no data)."""
    if score is None:
        return None
    if score > deadband:
        return "positive"   # long gamma -> rangebound, fade breakouts
    if score < -deadband:
        return "negative"   # short gamma -> trending, favor breakouts
    return "neutral"


# --- prediction markets (Kalshi) --------------------------------------------

def interpolate_probability_above(strikes_with_prob: list[tuple[float, float]], spot: float) -> float | None:
    """Given (strike, P(index above strike)) pairs sorted or unsorted, linearly
    interpolate P(index above spot). Probability decreases as strike increases,
    so this is interpolating a monotonically non-increasing step function.

    Strikes outside the available range clamp to the nearest endpoint's
    probability (flat extrapolation) rather than guessing. None if given no data.
    """
    if not strikes_with_prob:
        return None
    points = sorted(strikes_with_prob, key=lambda p: p[0])

    if spot <= points[0][0]:
        return points[0][1]
    if spot >= points[-1][0]:
        return points[-1][1]

    for (strike_lo, prob_lo), (strike_hi, prob_hi) in zip(points, points[1:]):
        if strike_lo <= spot <= strike_hi:
            if strike_hi == strike_lo:
                return prob_lo
            fraction = (spot - strike_lo) / (strike_hi - strike_lo)
            return prob_lo + fraction * (prob_hi - prob_lo)
    return None


def prediction_market_score(strikes_with_prob: list[tuple[float, float]], spot: float) -> float | None:
    """Converts P(index closes above current spot) into a -1..1 directional score."""
    prob_above = interpolate_probability_above(strikes_with_prob, spot)
    if prob_above is None:
        return None
    return _clip(2 * prob_above - 1)


# --- volatility regime (VIX / VIX9D / VVIX) --------------------------------

def term_structure_score(vix9d: float, vix: float, scale: float = 0.15) -> float | None:
    """VIX9D > VIX (backwardation) signals near-term stress -> bearish lean.
    VIX9D < VIX (contango) is the calm/normal state -> mildly bullish lean."""
    if vix is None or vix9d is None or vix == 0:
        return None
    relative_diff = (vix9d - vix) / vix
    return _clip(-relative_diff / scale)


def vvix_score(vvix: float, baseline: float = 90.0, scale: float = 20.0) -> float | None:
    """Elevated VVIX (vol-of-vol) reflects hedging stress -> bearish lean."""
    if vvix is None:
        return None
    return _clip(-(vvix - baseline) / scale)


def compute_volatility_regime_score(vix9d: float | None, vix: float | None, vvix: float | None) -> float | None:
    scores = [
        s for s in (
            term_structure_score(vix9d, vix),
            vvix_score(vvix),
        ) if s is not None
    ]
    return sum(scores) / len(scores) if scores else None


# --- Trump / political headline tone (GDELT) --------------------------------

_TRUMP_BEARISH_WORDS = (
    "tariff", "tariffs", "sanction", "sanctions", "recession", "crash",
    "selloff", "sell-off", "threat", "threatens", "shutdown", "default",
    "crisis", "plunge", "warns", "war",
)
_TRUMP_BULLISH_WORDS = (
    "deal", "agreement", "rally", "boom", "growth", "record high",
    "rate cut", "ceasefire", "truce", "stimulus", "surge", "optimism",
)


def trump_headline_score(headlines: list[str] | None, scale: float = 3.0) -> float | None:
    """Simple bearish/bullish keyword lexicon over recent Trump-related market
    headlines - the same style of approach as the StockTwits bull/bear tagging,
    applied to news headlines instead of social posts. None only when there are
    no headlines at all; a tie in keyword hits still yields a neutral 0.0."""
    if not headlines:
        return None
    text = " ".join(headlines).lower()
    bearish_hits = sum(text.count(word) for word in _TRUMP_BEARISH_WORDS)
    bullish_hits = sum(text.count(word) for word in _TRUMP_BULLISH_WORDS)
    return _clip((bullish_hits - bearish_hits) / scale)
