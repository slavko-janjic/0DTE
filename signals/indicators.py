"""Pure, testable functions that turn raw market data into -1..1 (bearish..bullish)
subscores for each signal category. Deliberately take plain lists/numbers rather
than DataFrames so they're easy to unit test with mocked inputs - the worker loop
is responsible for extracting these primitives from the data/sentiment layers.

All normalization scales below are arbitrary starting points per the project
brief - expect to tune them once real paper-trading data accumulates.
"""


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


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


def compute_technicals_score(closes: list[float], volumes: list[float]) -> float | None:
    scores = [
        s for s in (
            price_vs_vwap_score(closes[-1], vwap(closes, volumes)) if closes else None,
            momentum_score(closes),
            rsi_score(rsi(closes)),
        ) if s is not None
    ]
    return sum(scores) / len(scores) if scores else None


# --- greeks / IV ---------------------------------------------------------

def iv_skew_score(call_iv_atm: float | None, put_iv_atm: float | None, scale: float = 0.05) -> float | None:
    """Higher call IV relative to put IV suggests more upside speculation (bullish lean)."""
    if call_iv_atm is None or put_iv_atm is None:
        return None
    return _clip((call_iv_atm - put_iv_atm) / scale)


def compute_greeks_iv_score(call_iv_atm: float | None, put_iv_atm: float | None) -> float | None:
    return iv_skew_score(call_iv_atm, put_iv_atm)


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
