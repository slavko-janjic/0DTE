"""Kalshi public market data - no API key needed for read-only market data.

Kalshi runs daily binary "will the index close above/below X" ladders for
the S&P 500 (series KXINX) and Nasdaq-100 (series KXNASDAQ100), settled
against real cash. Each strike's yes bid/ask is the crowd's live implied
probability - a genuinely different signal from technicals/greeks/sentiment.
Verified live against https://external-api.kalshi.com/trade-api/v2 before
building this (see conversation - Tradestie taught us to check first).
"""
import requests

_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
_TIMEOUT = 10

# maps our tickers to (Kalshi series ticker, yfinance index ticker for the spot price)
TICKER_SERIES_MAP = {
    "SPY": ("KXINX", "^GSPC"),
    "QQQ": ("KXNASDAQ100", "^NDX"),
}


def get_price_distribution(series_ticker: str) -> list[dict] | None:
    """The nearest daily event's full probability distribution over price
    buckets: [{"floor", "cap", "prob"}] where floor/cap may be None for a tail.

    Kalshi quotes these as ~28 'between' buckets (7825-7850, 7850-7875, ...)
    plus two tails: one 'greater' than the top strike and one 'less' than the
    bottom. The buckets ARE the distribution.

    This previously kept ONLY the single 'greater' market and called it a
    "ladder". That left a 1-point ladder pinned at the far upper tail - so
    P(above spot) was really P(above the highest strike in the market), i.e.
    ~0.5%, and the signal sat at -0.99 (maximum bearish) for its entire life
    while carrying 15% of the composite's weight on SPY and QQQ.

    None on any failure or if no buckets are found.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/markets",
            params={"series_ticker": series_ticker, "status": "open", "limit": 200},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        markets = resp.json().get("markets", [])
    except Exception:
        return None

    if not markets:
        return None

    nearest_event = min(markets, key=lambda m: m.get("occurrence_datetime", ""))["event_ticker"]
    buckets = []
    for market in markets:
        if market.get("event_ticker") != nearest_event:
            continue
        strike_type = market.get("strike_type")
        if strike_type not in ("between", "greater", "less"):
            continue
        try:
            yes_bid = float(market["yes_bid_dollars"])
            yes_ask = float(market["yes_ask_dollars"])
            prob = (yes_bid + yes_ask) / 2.0
            floor = market.get("floor_strike")
            cap = market.get("cap_strike")
            buckets.append({
                "floor": float(floor) if floor is not None else None,
                "cap": float(cap) if cap is not None else None,
                "prob": prob,
            })
        except (KeyError, TypeError, ValueError):
            continue

    return buckets or None
