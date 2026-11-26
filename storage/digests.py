"""Digest cards and LLM usage.

digests: one row per card build - the text the agents and the Market page
read. llm_usage: one row per model call, so token use can be measured per
purpose (digest card, later each agent) before it surprises anyone.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,               -- crowd | news | filings | market | calendar
    created_at TEXT NOT NULL,         -- UTC ISO
    content TEXT NOT NULL,            -- markdown, what agents and the page read
    input_chars INTEGER,              -- size of the data the model was given
    model TEXT,                       -- NULL when no model was needed (no data)
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_digests_kind_time ON digests(kind, created_at DESC);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    purpose TEXT NOT NULL,            -- e.g. digest:news, agent:momentum
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd REAL,                    -- API-rate equivalent the SDK reports
    duration_ms INTEGER,
    ok INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_at ON llm_usage(at);
"""


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _connect(path):
    from storage.db import BUSY_TIMEOUT_SECONDS
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_digest(path: str | Path, kind: str, content: str, *, input_chars: int | None = None,
                  model: str | None = None, ok: bool = True, error: str | None = None) -> int:
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO digests (kind, created_at, content, input_chars, model, ok, error) "
            "VALUES (?,?,?,?,?,?,?)", (kind, _now(), content, input_chars, model, int(ok), error))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def latest_digests(path: str | Path, ok_only: bool = True) -> dict[str, sqlite3.Row]:
    """Newest card per kind (the last good one, unless ok_only=False)."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT d.* FROM digests d JOIN (SELECT kind, MAX(id) AS id FROM digests "
            + ("WHERE ok = 1 " if ok_only else "") + "GROUP BY kind) m ON d.id = m.id").fetchall()
        return {r["kind"]: r for r in rows}
    finally:
        conn.close()


def last_digest_run(path: str | Path) -> str | None:
    """When any card was last attempted - the scheduler's bookmark."""
    conn = _connect(path)
    try:
        row = conn.execute("SELECT MAX(created_at) AS t FROM digests").fetchone()
        return row["t"] if row else None
    finally:
        conn.close()


def log_llm_usage(path: str | Path, purpose: str, *, model: str | None, usage: dict | None,
                  cost_usd: float | None, duration_ms: int | None, ok: bool,
                  error: str | None = None) -> None:
    u = usage or {}
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO llm_usage (at, purpose, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_usd, duration_ms, ok, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), purpose, model, u.get("input_tokens"), u.get("output_tokens"),
             u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"),
             cost_usd, duration_ms, int(ok), error))
        conn.commit()
    finally:
        conn.close()


def usage_summary(path: str | Path, hours: float = 24) -> dict[str, dict]:
    """Per purpose prefix (digest, agent) over the window: calls and tokens."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT purpose, COUNT(*) AS calls, SUM(COALESCE(input_tokens,0) + "
            "COALESCE(cache_read_tokens,0) + COALESCE(cache_write_tokens,0)) AS tokens_in, "
            "SUM(COALESCE(output_tokens,0)) AS tokens_out, SUM(COALESCE(cost_usd,0)) AS cost, "
            "SUM(1 - ok) AS failed FROM llm_usage WHERE at >= ? GROUP BY purpose",
            (since,)).fetchall()
        return {r["purpose"]: dict(r) for r in rows}
    finally:
        conn.close()
