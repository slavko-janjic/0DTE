"""Raw Reddit post text via Reddit's public, unauthenticated JSON search - no
app registration, client ID/secret, or login required.

As of this writing, Reddit's Cloudflare bot-protection blocks this endpoint
with a 403 challenge page regardless of headers used (verified: browser-like
headers don't get past it, unlike StockTwits which only needed those). It's
kept here as best-effort since Reddit's blocking posture changes over time
and this may start working again without any code change - if it stays
blocked, get_recent_posts() just returns an empty list and the rest of the
pipeline degrades gracefully, same as any other unavailable source.
"""
import requests

_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_recent_posts(ticker: str, subreddits: list[str], limit_per_sub: int = 5) -> list[str]:
    """Returns recent post titles mentioning the ticker, across the given subreddits.

    Best-effort: returns an empty list on any failure (network, rate limit,
    bot-protection block) rather than raising.
    """
    titles: list[str] = []
    for sub_name in subreddits:
        url = f"https://www.reddit.com/r/{sub_name}/search.json"
        params = {
            "q": ticker, "restrict_sr": 1, "sort": "new", "t": "day", "limit": limit_per_sub,
        }
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
            titles.extend(child["data"]["title"] for child in children if "data" in child)
        except Exception:
            continue
    return titles
