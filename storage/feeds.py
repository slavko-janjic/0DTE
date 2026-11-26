"""Tables for the outside-world feeds the LLM agents read: Reddit posts, news
headlines, SEC filings, Reddit mention counts (via ApeWisdom), plus the ticker universe used to tag them and a status
row per feed. Written by feeds.py (its own scheduled task), read by the digest
builders and the web UI. Kept apart from storage/db.py so the trading tables
and these research tables can change independently; db.init_db creates both.

Every collector stores raw items only - no LLM calls here. Interpretation is
the digest builders' job (phase 2).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reddit_mentions (
    taken_at TEXT NOT NULL,           -- UTC ISO, one value per collection run
    filter TEXT NOT NULL,             -- ApeWisdom filter: wallstreetbets, options, all-stocks, ...
    ticker TEXT NOT NULL,
    name TEXT,
    rank INTEGER,
    mentions INTEGER,                 -- over the trailing 24 hours
    upvotes INTEGER,
    rank_24h_ago INTEGER,
    mentions_24h_ago INTEGER,
    PRIMARY KEY (taken_at, filter, ticker)
);
CREATE INDEX IF NOT EXISTS idx_reddit_mentions_ticker ON reddit_mentions(ticker, taken_at);

CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY,              -- feed guid, or the link when there is none
    source TEXT NOT NULL,             -- feed name from config
    title TEXT NOT NULL,
    summary TEXT,
    link TEXT,
    published_at TEXT,                -- UTC ISO; NULL when the feed gives none
    tickers_json TEXT NOT NULL,
    first_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at);

CREATE TABLE IF NOT EXISTS sec_filings (
    accession TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    cik INTEGER NOT NULL,
    form TEXT NOT NULL,
    filed_on TEXT NOT NULL,           -- YYYY-MM-DD
    description TEXT,
    link TEXT NOT NULL,
    first_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filings_filed ON sec_filings(filed_on);

CREATE TABLE IF NOT EXISTS symbols (
    ticker TEXT PRIMARY KEY,
    cik INTEGER,
    name TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_status (
    feed TEXT PRIMARY KEY,            -- 'reddit' | 'news' | 'filings' | 'symbols'
    last_run TEXT,
    last_ok TEXT,
    last_error TEXT,                  -- NULL after a clean run
    items_new INTEGER                 -- new rows on the last run
);
"""


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _connect(path: str | Path) -> sqlite3.Connection:
    # same timeout as storage.db.connect; local import avoids a cycle
    from storage.db import BUSY_TIMEOUT_SECONDS
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# --- reddit (ApeWisdom mention snapshots) ------------------------------------

def insert_reddit_mentions(path: str | Path, rows: list[dict], taken_at: str | None = None) -> int:
    """Store one snapshot (every row shares taken_at). Returns rows stored."""
    taken_at = taken_at or _now()
    conn = _connect(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO reddit_mentions (taken_at, filter, ticker, name, rank, "
            "mentions, upvotes, rank_24h_ago, mentions_24h_ago) VALUES (?,?,?,?,?,?,?,?,?)",
            [(taken_at, r["filter"], r["ticker"], r.get("name"), r.get("rank"), r.get("mentions"),
              r.get("upvotes"), r.get("rank_24h_ago"), r.get("mentions_24h_ago")) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def latest_reddit_mentions(path: str | Path, filter_name: str = "all-stocks",
                           limit: int = 50) -> list[sqlite3.Row]:
    """The most recent snapshot for one filter, best rank first."""
    conn = _connect(path)
    try:
        row = conn.execute("SELECT MAX(taken_at) AS t FROM reddit_mentions WHERE filter = ?",
                           (filter_name,)).fetchone()
        if not row or not row["t"]:
            return []
        return conn.execute(
            "SELECT * FROM reddit_mentions WHERE filter = ? AND taken_at = ? "
            "ORDER BY rank LIMIT ?", (filter_name, row["t"], limit)).fetchall()
    finally:
        conn.close()


def reddit_mention_history(path: str | Path, ticker: str, filter_name: str = "all-stocks",
                           hours: float = 48) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute(
            "SELECT * FROM reddit_mentions WHERE ticker = ? AND filter = ? AND taken_at >= ? "
            "ORDER BY taken_at", (ticker.upper(), filter_name, _since(hours))).fetchall()
    finally:
        conn.close()


# --- news ---------------------------------------------------------------------

def insert_news_items(path: str | Path, items: list[dict]) -> int:
    """Insert headlines not seen before; a headline already stored keeps its
    tickers and gains any new ones (the same story arrives via several ticker
    feeds). Returns how many were new."""
    now = _now()
    new = 0
    conn = _connect(path)
    try:
        for it in items:
            row = conn.execute("SELECT tickers_json FROM news_items WHERE id = ?",
                               (it["id"],)).fetchone()
            if row:
                merged = sorted(set(json.loads(row["tickers_json"])) | set(it.get("tickers", [])))
                conn.execute("UPDATE news_items SET tickers_json = ? WHERE id = ?",
                             (json.dumps(merged), it["id"]))
                continue
            conn.execute(
                "INSERT INTO news_items (id, source, title, summary, link, published_at, "
                "tickers_json, first_seen) VALUES (?,?,?,?,?,?,?,?)",
                (it["id"], it["source"], it["title"], it.get("summary"), it.get("link"),
                 it.get("published_at"), json.dumps(sorted(set(it.get("tickers", [])))), now))
            new += 1
        conn.commit()
    finally:
        conn.close()
    return new


def recent_news_items(path: str | Path, hours: float = 24, limit: int = 500) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute(
            "SELECT * FROM news_items WHERE COALESCE(published_at, first_seen) >= ? "
            "ORDER BY COALESCE(published_at, first_seen) DESC LIMIT ?",
            (_since(hours), limit)).fetchall()
    finally:
        conn.close()


# --- SEC filings --------------------------------------------------------------

def insert_filings(path: str | Path, filings: list[dict]) -> int:
    now = _now()
    conn = _connect(path)
    try:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO sec_filings (accession, ticker, cik, form, filed_on, "
            "description, link, first_seen) VALUES (?,?,?,?,?,?,?,?)",
            [(f["accession"], f["ticker"], f["cik"], f["form"], f["filed_on"],
              f.get("description"), f["link"], now) for f in filings])
        new = conn.total_changes - before
        conn.commit()
    finally:
        conn.close()
    return new


def recent_filings(path: str | Path, days: int = 7, limit: int = 200) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    conn = _connect(path)
    try:
        return conn.execute(
            "SELECT * FROM sec_filings WHERE filed_on >= ? ORDER BY filed_on DESC LIMIT ?",
            (cutoff, limit)).fetchall()
    finally:
        conn.close()


# --- symbols (ticker universe) ------------------------------------------------

def replace_symbols(path: str | Path, symbols: list[dict]) -> int:
    """Swap in a fresh ticker universe (SEC's company list). Done in one
    transaction so readers never see it half-filled."""
    now = _now()
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM symbols")
        conn.executemany(
            "INSERT OR REPLACE INTO symbols (ticker, cik, name, updated_at) VALUES (?,?,?,?)",
            [(s["ticker"].upper(), s.get("cik"), s.get("name"), now) for s in symbols])
        conn.commit()
        return len(symbols)
    finally:
        conn.close()


def symbol_universe(path: str | Path) -> set[str]:
    conn = _connect(path)
    try:
        return {r["ticker"] for r in conn.execute("SELECT ticker FROM symbols")}
    finally:
        conn.close()


def cik_for(path: str | Path, ticker: str) -> int | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT cik FROM symbols WHERE ticker = ?",
                           (ticker.upper(),)).fetchone()
        return row["cik"] if row and row["cik"] is not None else None
    finally:
        conn.close()


def symbols_updated_at(path: str | Path) -> str | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT MAX(updated_at) AS t FROM symbols").fetchone()
        return row["t"] if row else None
    finally:
        conn.close()


# --- mentions -----------------------------------------------------------------

def ticker_mentions(path: str | Path, hours: float = 24) -> dict[str, dict]:
    """Per ticker: Reddit mentions and upvotes over the last 24 hours (latest
    ApeWisdom snapshot, all stock subreddits), the change against 24 hours
    before, and news headlines tagged with it over `hours`. The raw material
    for the 'most talked about' digest card."""
    out: dict[str, dict] = {}

    def slot(t):
        return out.setdefault(t, {"reddit_mentions": 0, "reddit_mentions_24h_ago": None,
                                  "reddit_upvotes": 0, "reddit_rank": None, "news": 0})

    for row in latest_reddit_mentions(path, "all-stocks", limit=1000):
        s = slot(row["ticker"])
        s["reddit_mentions"] = row["mentions"] or 0
        s["reddit_mentions_24h_ago"] = row["mentions_24h_ago"]
        s["reddit_upvotes"] = row["upvotes"] or 0
        s["reddit_rank"] = row["rank"]
    for row in recent_news_items(path, hours, limit=5000):
        for t in json.loads(row["tickers_json"]):
            slot(t)["news"] += 1
    return out


# --- feed status --------------------------------------------------------------

def record_feed_run(path: str | Path, feed: str, items_new: int | None,
                    error: str | None = None) -> None:
    now = _now()
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO feed_status (feed, last_run, last_ok, last_error, items_new) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(feed) DO UPDATE SET "
            "last_run = excluded.last_run, "
            "last_ok = COALESCE(excluded.last_ok, feed_status.last_ok), "
            "last_error = excluded.last_error, items_new = excluded.items_new",
            (feed, now, None if error else now, error, items_new))
        conn.commit()
    finally:
        conn.close()


def feed_status(path: str | Path) -> dict[str, dict]:
    conn = _connect(path)
    try:
        return {r["feed"]: dict(r) for r in conn.execute("SELECT * FROM feed_status")}
    finally:
        conn.close()
