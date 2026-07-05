"""Free, no-key ApeWisdom API client - aggregated Reddit mention/hype data.

ApeWisdom (https://apewisdom.io) tracks ticker mentions across subreddits.
It doesn't publish a bull/bear sentiment score, so we derive a momentum-based
proxy: tickers gaining mentions/rank quickly relative to 24h ago are treated
as more bullish-leaning attention, tickers losing mentions as fading. This is
a rough proxy by design - it's one of several sentiment inputs, not gospel.
"""
import requests

_BASE_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"
_TIMEOUT = 10


def get_mention_momentum(ticker: str) -> float | None:
    """Returns a -1..1 score: positive = rising mentions/rank, negative = fading.

    Returns None if the ticker isn't found or the request fails.
    """
    try:
        resp = requests.get(_BASE_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        return None

    entry = next((r for r in results if r.get("ticker", "").upper() == ticker.upper()), None)
    if entry is None:
        return None

    try:
        mentions = float(entry.get("mentions", 0))
        mentions_24h_ago = float(entry.get("mentions_24h_ago", 0)) or 1.0
        change_ratio = (mentions - mentions_24h_ago) / mentions_24h_ago
        # squash unbounded % change into -1..1
        return max(-1.0, min(1.0, change_ratio))
    except (TypeError, ValueError):
        return None
