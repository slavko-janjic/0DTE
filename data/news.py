"""News collector: headlines from RSS feeds.

Two kinds of feed, both from config `feeds.news`:
- general feeds (CNBC, MarketWatch, Seeking Alpha market currents, ...):
  tickers are found in the title + summary;
- one Yahoo Finance feed per watchlist ticker: everything there is about that
  ticker, so it is tagged with it whatever the text says.

Headlines only - no article bodies are fetched.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone

from sentiment.tickers import extract_tickers

USER_AGENT = "Mozilla/5.0 (0DTE research tool)"
YAHOO_TICKER_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}"
SUMMARY_MAX_CHARS = 600

DEFAULT_FEEDS = {
    "cnbc_top": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "marketwatch_top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "seekingalpha_currents": "https://seekingalpha.com/market_currents.xml",
}


def _clean(html: str | None) -> str | None:
    if not html:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return text[:SUMMARY_MAX_CHARS] or None


def parse_feed(parsed, source: str, universe: set[str], always: set[str],
               tag: str | None = None) -> list[dict]:
    """Items from a feedparser result. `tag` forces a ticker onto every item
    (a per-ticker feed)."""
    out = []
    for e in parsed.entries:
        title = (e.get("title") or "").strip()
        link = e.get("link")
        ident = e.get("id") or e.get("guid") or link
        if not title or not ident:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        published = (datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc).isoformat()
                     if stamp else None)
        summary = _clean(e.get("summary"))
        tickers = set(extract_tickers(f"{title}\n{summary or ''}", universe, always))
        if tag:
            tickers.add(tag)
        out.append({"id": ident, "source": source, "title": title, "summary": summary,
                    "link": link, "published_at": published, "tickers": sorted(tickers)})
    return out


def feed_list(config: dict) -> list[tuple[str, str, str | None]]:
    """(source name, url, forced ticker) for every feed to read this run."""
    cfg = config.get("feeds", {}).get("news", {})
    general = cfg.get("feeds", DEFAULT_FEEDS)
    out = [(name, url, None) for name, url in general.items()]
    if cfg.get("per_ticker_yahoo", True):
        for t in config.get("feeds", {}).get("watchlist", []):
            out.append((f"yahoo_{t}", YAHOO_TICKER_FEED.format(ticker=t), t))
    return out


def collect(config: dict, universe: set[str], timeout: float = 15) -> tuple[list[dict], list[str]]:
    """All items from all feeds, plus a list of per-feed errors (one broken
    feed doesn't stop the rest)."""
    import feedparser
    import requests
    always = set(config.get("feeds", {}).get("watchlist", []))
    items, errors = [], []
    for source, url, tag in feed_list(config):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            items.extend(parse_feed(feedparser.parse(resp.content), source, universe, always, tag))
        except Exception as exc:  # noqa: BLE001 - recorded, next feed continues
            errors.append(f"{source}: {exc}")
    return items, errors
