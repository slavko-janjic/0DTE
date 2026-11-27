"""What the LLM agents think and do: a journal entry for every decision
(written BEFORE any order it leads to), and one row per wake-up with its
outcome and token use."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    run_id INTEGER,
    at TEXT NOT NULL,
    action TEXT NOT NULL,             -- open | add | reduce | close | no_action | note
    ticker TEXT,
    thesis TEXT NOT NULL,
    evidence TEXT,
    confidence INTEGER,               -- 0-100, the agent's own
    target REAL,
    stop REAL,
    hold_until TEXT,                  -- YYYY-MM-DD, planned exit date
    orders_json TEXT                  -- results of orders placed under this entry
);
CREATE INDEX IF NOT EXISTS idx_agent_journal ON agent_journal(agent, at DESC);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL,               -- live | dry_run
    status TEXT NOT NULL,             -- running | ok | error | timeout
    summary TEXT,                     -- the agent's closing line
    orders INTEGER NOT NULL DEFAULT 0,
    turns INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs ON agent_runs(agent, started_at DESC);
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


def add_entry(path: str | Path, agent: str, run_id: int | None, action: str, thesis: str, *,
              ticker: str | None = None, evidence: str | None = None, confidence: int | None = None,
              target: float | None = None, stop: float | None = None,
              hold_until: str | None = None) -> int:
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO agent_journal (agent, run_id, at, action, ticker, thesis, evidence, confidence, "
            "target, stop, hold_until) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (agent, run_id, _now(), action, ticker, thesis, evidence, confidence, target, stop, hold_until))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_entry(path: str | Path, entry_id: int) -> sqlite3.Row | None:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_journal WHERE id = ?", (entry_id,)).fetchone()
    finally:
        conn.close()


def attach_order(path: str | Path, entry_id: int, result: dict) -> None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT orders_json FROM agent_journal WHERE id = ?", (entry_id,)).fetchone()
        orders = json.loads(row["orders_json"]) if row and row["orders_json"] else []
        orders.append(result)
        conn.execute("UPDATE agent_journal SET orders_json = ? WHERE id = ?", (json.dumps(orders), entry_id))
        conn.commit()
    finally:
        conn.close()


def recent_entries(path: str | Path, agent: str, limit: int = 10) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_journal WHERE agent = ? ORDER BY id DESC LIMIT ?",
                            (agent, limit)).fetchall()
    finally:
        conn.close()


def start_run(path: str | Path, agent: str, mode: str) -> int:
    conn = _connect(path)
    try:
        cur = conn.execute("INSERT INTO agent_runs (agent, started_at, mode, status) VALUES (?,?,?,?)",
                           (agent, _now(), mode, "running"))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_run(path: str | Path, run_id: int, status: str, *, summary: str | None = None,
               orders: int = 0, turns: int | None = None, tokens_in: int | None = None,
               tokens_out: int | None = None, cost_usd: float | None = None,
               error: str | None = None) -> None:
    conn = _connect(path)
    try:
        conn.execute("UPDATE agent_runs SET finished_at = ?, status = ?, summary = ?, orders = ?, turns = ?, "
                     "tokens_in = ?, tokens_out = ?, cost_usd = ?, error = ? WHERE id = ?",
                     (_now(), status, summary, orders, turns, tokens_in, tokens_out, cost_usd, error, run_id))
        conn.commit()
    finally:
        conn.close()


def last_run_started(path: str | Path, agent: str) -> str | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT MAX(started_at) AS t FROM agent_runs WHERE agent = ?", (agent,)).fetchone()
        return row["t"] if row else None
    finally:
        conn.close()


def recent_runs(path: str | Path, agent: str, limit: int = 20) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT ?",
                            (agent, limit)).fetchall()
    finally:
        conn.close()


def tokens_today(path: str | Path, agent: str, since_iso: str) -> int:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT SUM(COALESCE(tokens_in,0) + COALESCE(tokens_out,0)) AS t FROM agent_runs "
                           "WHERE agent = ? AND started_at >= ?", (agent, since_iso)).fetchone()
        return row["t"] or 0
    finally:
        conn.close()
