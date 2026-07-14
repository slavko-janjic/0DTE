"""GDELT's free DOC 2.0 API - no key needed. Used to gauge how much Trump-related
political/economic news (tariffs, Fed pressure, trade deals, etc.) is moving
through the news cycle right now, and its rough tone, as a proxy for the kind
of headline-driven rhetoric that shifts markets intraday.
"""
import requests

_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_TIMEOUT = 10
_QUERY = "Trump (tariff OR tariffs OR economy OR trade OR fed OR market OR stocks)"


def get_trump_market_headlines(timespan_hours: int = 6, max_records: int = 20) -> list[str] | None:
    try:
        resp = requests.get(
            _BASE_URL,
            params={
                "query": _QUERY,
                "mode": "artlist",
                "maxrecords": max_records,
                "format": "json",
                "timespan": f"{timespan_hours}h",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
    except Exception:
        return None
    headlines = [a["title"] for a in articles if a.get("title")]
    return headlines or None
