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


def get_strike_ladder(series_ticker: str) -> list[tuple[float, float]] | None:
    """Returns [(floor_strike, prob_above)] for the nearest upcoming daily
    event in this series, using only 'greater than' strike markets (which
    directly give P(index above strike) from the yes bid/ask midpoint).

    None on any failure or if no 'greater' markets are found.
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
    ladder = []
    for market in markets:
        if market.get("event_ticker") != nearest_event:
            continue
        if market.get("strike_type") != "greater" or "floor_strike" not in market:
            continue
        try:
            yes_bid = float(market["yes_bid_dollars"])
            yes_ask = float(market["yes_ask_dollars"])
            prob_above = (yes_bid + yes_ask) / 2.0
            ladder.append((float(market["floor_strike"]), prob_above))
        except (KeyError, TypeError, ValueError):
            continue

    return ladder or None
