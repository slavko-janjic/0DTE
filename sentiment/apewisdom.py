"""Reddit chatter via ApeWisdom (https://apewisdom.io): per-ticker mention
counts, upvotes and rank on r/wallstreetbets, r/options, r/stocks and others,
with the same numbers 24 hours earlier.

Why not Reddit itself: Reddit stopped taking new API applications on
2026-10-31, shut its RSS feeds on 2026-11-13 and closes the rest of its
public API in March 2027. ApeWisdom is free, needs no key, and is the
aggregate the agents need anyway ("what is the crowd piling into, and is it
growing"). It has no post text, so there is no tone - the digests read
mention spikes and rank jumps instead.

Each run stores one snapshot per subreddit filter; history builds up so the
digests can see a mention spike forming through the day.
"""
from __future__ import annotations

import html

API_URL = "https://apewisdom.io/api/v1.0/filter/{filter}/page/{page}"
USER_AGENT = "0DTE research tool"
DEFAULT_FILTERS = ["all-stocks", "wallstreetbets", "options", "stocks", "investing"]


def parse_page(payload: dict, filter_name: str) -> list[dict]:
    out = []
    for r in payload.get("results") or []:
        ticker = (r.get("ticker") or "").upper()
        if not ticker:
            continue
        out.append({
            "filter": filter_name,
            "ticker": ticker,
            "name": html.unescape(r.get("name") or "") or None,
            "rank": _int(r.get("rank")),
            "mentions": _int(r.get("mentions")),
            "upvotes": _int(r.get("upvotes")),
            "rank_24h_ago": _int(r.get("rank_24h_ago")),
            "mentions_24h_ago": _int(r.get("mentions_24h_ago")),
        })
    return out


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def collect(config: dict, timeout: float = 15) -> tuple[list[dict], list[str]]:
    """Top tickers for each configured filter (first `pages` pages of 100)."""
    import requests
    cfg = config.get("feeds", {}).get("reddit", {})
    filters = cfg.get("filters", DEFAULT_FILTERS)
    pages = cfg.get("pages", 1)
    rows, errors = [], []
    for f in filters:
        for page in range(1, pages + 1):
            try:
                resp = requests.get(API_URL.format(filter=f, page=page),
                                    headers={"User-Agent": USER_AGENT}, timeout=timeout)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f}: {exc}")
                break
            rows.extend(parse_page(payload, f))
            if page >= (payload.get("pages") or 0):
                break
    return rows, errors
