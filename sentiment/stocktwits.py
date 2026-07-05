"""Free public StockTwits API client - self-tagged Bullish/Bearish messages.

No API key needed for the public symbol stream endpoint. Messages are
optionally tagged by their author as Bullish or Bearish; untagged messages
are ignored for scoring.
"""
import requests

_TIMEOUT = 10


def get_bull_bear_score(ticker: str, limit: int = 30) -> float | None:
    """Returns (bullish - bearish) / (bullish + bearish) among the most recent
    tagged messages, in -1..1. None if unavailable or no tagged messages.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        messages = resp.json().get("messages", [])[:limit]
    except Exception:
        return None

    bullish = bearish = 0
    for msg in messages:
        sentiment = (msg.get("entities") or {}).get("sentiment")
        if not sentiment:
            continue
        basic = sentiment.get("basic")
        if basic == "Bullish":
            bullish += 1
        elif basic == "Bearish":
            bearish += 1

    total = bullish + bearish
    if total == 0:
        return None
    return (bullish - bearish) / total
