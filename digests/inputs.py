"""Build the data each digest card is written from: plain, compact text the
model reads (and a person can read too, for checking). No model calls here -
every function is deterministic and tested on its own.

Each returns "" when there is nothing to report, and the card is then stored
as "no data" without spending a model call.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from storage import db as storage
from storage import feeds as feed_store

NEWS_HOURS = 18
NEWS_MAX_ITEMS = 90
FILINGS_DAYS = 3
CALENDAR_DAYS = 7


def _pct_change(now: int | None, before: int | None) -> str:
    if not now or not before:
        return "new" if now and not before else "n/a"
    return f"{(now - before) / before * 100:+.0f}%"


def crowd(db_path: str, config: dict) -> str:
    """Reddit mention table: overall leaders, the biggest risers, and the top
    of r/wallstreetbets and r/options."""
    rows = feed_store.latest_reddit_mentions(db_path, "all-stocks", limit=100)
    if not rows:
        return ""
    lines = [f"Snapshot taken {rows[0]['taken_at'][:16]} UTC. Mentions = last 24h across stock subreddits.",
             "", "TOP 20 BY MENTIONS (rank, ticker, mentions, 24h-before, change, rank 24h before, upvotes):"]
    for r in rows[:20]:
        lines.append(f"{r['rank']}. {r['ticker']} ({r['name']}): {r['mentions']} vs "
                     f"{r['mentions_24h_ago']} ({_pct_change(r['mentions'], r['mentions_24h_ago'])}), "
                     f"was #{r['rank_24h_ago']}, {r['upvotes']} upvotes")
    risers = sorted((r for r in rows if (r["mentions"] or 0) >= 15),
                    key=lambda r: (r["mentions"] or 0) - (r["mentions_24h_ago"] or 0), reverse=True)[:8]
    lines += ["", "BIGGEST RISERS (at least 15 mentions):"]
    lines += [f"{r['ticker']}: {r['mentions']} vs {r['mentions_24h_ago']} "
              f"({_pct_change(r['mentions'], r['mentions_24h_ago'])})" for r in risers]
    for name in ("wallstreetbets", "options"):
        sub = feed_store.latest_reddit_mentions(db_path, name, limit=8)
        if sub:
            lines += ["", f"TOP OF r/{name}:"]
            lines.append(", ".join(f"{r['ticker']} {r['mentions']}" for r in sub))
    watch = set(config.get("feeds", {}).get("watchlist", []))
    lines += ["", "WATCHLIST: " + ", ".join(sorted(watch))]
    return "\n".join(lines)


def news(db_path: str, config: dict) -> str:
    rows = feed_store.recent_news_items(db_path, hours=NEWS_HOURS, limit=NEWS_MAX_ITEMS)
    if not rows:
        return ""
    lines = [f"Headlines from the last {NEWS_HOURS} hours, newest first "
             "(time UTC | source | tickers | title):"]
    for r in rows:
        t = (r["published_at"] or r["first_seen"])[5:16].replace("T", " ")
        tickers = ",".join(json.loads(r["tickers_json"])) or "-"
        lines.append(f"{t} | {r['source']} | {tickers} | {r['title']}")
    return "\n".join(lines)


def filings(db_path: str, config: dict) -> str:
    rows = feed_store.recent_filings(db_path, days=FILINGS_DAYS, limit=150)
    if not rows:
        return ""
    lines = [f"SEC filings for the watchlist, last {FILINGS_DAYS} days (date | ticker | form | description):",
             "Form 4 = insider transaction report; 8-K = material event."]
    for r in rows:
        lines.append(f"{r['filed_on']} | {r['ticker']} | {r['form']} | {r['description'] or ''}")
    return "\n".join(lines)


def market(db_path: str, config: dict) -> str:
    """The worker's own read of QQQ/SPY: latest composite signal, today's
    pre-market setup, VIX term structure."""
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    today = datetime.now(tz).date().isoformat()
    lines = []
    for ticker in config.get("tickers", []):
        sig = storage.get_latest_signal(db_path, ticker)
        if sig:
            keys = sig.keys()
            sub = json.loads(sig["subscores_json"])
            sub_txt = ", ".join(f"{k} {v:+.2f}" if isinstance(v, (int, float)) else f"{k} {v}"
                                for k, v in sub.items())
            extra = ""
            if "gamma_regime" in keys and sig["gamma_regime"]:
                extra = f", gamma regime {sig['gamma_regime']}"
            lines.append(f"{ticker} at {sig['timestamp'][:16]} UTC: spot {sig['spot_price']}, "
                         f"direction {sig['direction']}, confidence {sig['confidence']:.0f}%, "
                         f"recommendation '{sig['recommendation']}'{extra}; subscores: {sub_txt}")
        setup = storage.get_day_setup(db_path, ticker, today)
        if setup:
            keep = {k: setup.get(k) for k in ("gap_pct", "gap_direction", "prior_high", "prior_low",
                                              "prior_close", "overnight_high", "overnight_low",
                                              "catalysts", "opening_bias")}
            lines.append(f"{ticker} pre-market setup today: {json.dumps(keep, default=str)}")
    with storage.connect(db_path) as conn:
        vix = conn.execute("SELECT * FROM vix_snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()
    if vix:
        lines.append(f"VIX {vix['vix']}, VIX9D {vix['vix9d']}, VVIX {vix['vvix']}, "
                     f"term ratio {vix['term_ratio']} (negative = contango = normal) at {vix['timestamp'][:16]} UTC")
    if not lines:
        return ""
    lines.insert(0, "Note: the worker's own research found direction calls near a coin flip; "
                    "treat direction/confidence as context, not a forecast.")
    return "\n".join(lines)


def calendar(db_path: str, config: dict, earnings_lookup=None,
             today: date | None = None) -> str:
    """Scheduled macro catalysts, market holidays/half days, and watchlist
    earnings in the next CALENDAR_DAYS days."""
    if earnings_lookup is None:
        from data.market_data import get_next_earnings_date as earnings_lookup
    tz = ZoneInfo(config.get("market_hours", {}).get("timezone", "America/New_York"))
    today = today or datetime.now(tz).date()
    end = today + timedelta(days=CALENDAR_DAYS)
    in_window = lambda d: today.isoformat() <= str(d) <= end.isoformat()  # noqa: E731
    lines = []
    for c in config.get("market_catalysts", []):
        if in_window(c["date"]):
            lines.append(f"{c['date']} {c.get('time', '')} ET | macro | {c['label']} ({c.get('impact', '')} impact)")
    for d in config.get("market_holidays", []):
        if in_window(d):
            lines.append(f"{d} | market closed (holiday)")
    for d in config.get("market_half_days", []):
        if in_window(d):
            lines.append(f"{d} | half day, early close 13:00 ET")
    for t in config.get("feeds", {}).get("watchlist", []):
        when = earnings_lookup(t)
        if when and in_window(when[:10]):
            lines.append(f"{when[:10]} | earnings | {t}")
    if not lines:
        return f"No scheduled catalysts, holidays or watchlist earnings between {today} and {end}."
    lines.sort()
    return f"Today is {today} ({today:%A}). Events through {end}:\n" + "\n".join(lines)


BUILDERS = {"crowd": crowd, "news": news, "filings": filings, "market": market, "calendar": calendar}
