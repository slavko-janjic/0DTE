"""Payload for the Market page: the latest digest card of each kind, the
Reddit mention leaders, feed health, and model usage over the last day."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from storage import digests as dstore
from storage import feeds as fstore

CARD_TITLES = {"market": "Market state", "calendar": "Calendar", "news": "News",
               "crowd": "Reddit crowd", "filings": "SEC filings"}
FEED_LABELS = {"news": "News", "reddit": "Reddit (ApeWisdom)", "filings": "SEC filings",
               "symbols": "Ticker list"}
# a feed is 'stale' when its last good run is older than this many intervals
STALE_INTERVALS = 3


def _age_minutes(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    return int((now - datetime.fromisoformat(iso)).total_seconds() // 60)


def market_payload(db_path: str, config: dict) -> dict:
    now = datetime.now(timezone.utc)
    cards_cfg = config.get("digests", {}).get("cards", list(CARD_TITLES))
    good = dstore.latest_digests(db_path)
    latest = dstore.latest_digests(db_path, ok_only=False)
    cards = []
    for kind in cards_cfg:
        row, last = good.get(kind), latest.get(kind)
        cards.append({
            "kind": kind,
            "title": CARD_TITLES.get(kind, kind.title()),
            "content": row["content"] if row else None,
            "built_at": row["created_at"] if row else None,
            "age_minutes": _age_minutes(row["created_at"] if row else None, now),
            "last_error": last["error"] if last is not None and not last["ok"] else None,
        })

    intervals = {"reddit": 30, "news": 15, "filings": 360,
                 **config.get("feeds", {}).get("intervals_minutes", {})}
    feeds = []
    for name, st in sorted(fstore.feed_status(db_path).items()):
        age = _age_minutes(st["last_ok"], now)
        limit = intervals.get(name, 7 * 24 * 60) * STALE_INTERVALS
        state = "error" if st["last_error"] and st["last_ok"] is None else (
            "stale" if age is None or age > limit else ("warn" if st["last_error"] else "ok"))
        feeds.append({"feed": name, "label": FEED_LABELS.get(name, name), "state": state,
                      "last_ok_minutes": age, "items_new": st["items_new"],
                      "error": st["last_error"]})

    crowd = []
    for r in fstore.latest_reddit_mentions(db_path, "all-stocks", limit=15):
        before = r["mentions_24h_ago"]
        crowd.append({"rank": r["rank"], "ticker": r["ticker"], "name": r["name"],
                      "mentions": r["mentions"], "mentions_24h_ago": before,
                      "change_pct": round((r["mentions"] - before) / before * 100)
                      if before and r["mentions"] is not None else None,
                      "rank_24h_ago": r["rank_24h_ago"]})

    usage = dstore.usage_summary(db_path, hours=24)
    digest_usage = [u for p, u in usage.items() if p.startswith("digest:")]
    return {
        "cards": cards,
        "feeds": feeds,
        "crowd": crowd,
        "usage_24h": {
            "calls": sum(u["calls"] for u in digest_usage),
            "tokens_in": sum(u["tokens_in"] or 0 for u in digest_usage),
            "tokens_out": sum(u["tokens_out"] or 0 for u in digest_usage),
            "failed": sum(u["failed"] or 0 for u in digest_usage),
        },
        "schedule": config.get("digests", {}).get("at", []),
    }
