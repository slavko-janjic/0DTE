"""Free public StockTwits API client - self-tagged Bullish/Bearish messages.

No API key needed for the public symbol stream endpoint, but it sits behind
Cloudflare bot-protection that blocks bare requests (no User-Agent/Accept
headers) with a 403 challenge page - a browser-like header set is enough to
pass, no JS execution needed.
"""
import requests

_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_bull_bear_score(ticker: str, limit: int = 30) -> float | None:
    """Returns (bullish - bearish) / (bullish + bearish) among the most recent
    tagged messages, in -1..1. None if unavailable or no tagged messages.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
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
