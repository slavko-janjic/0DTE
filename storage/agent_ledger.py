"""The LLM agents' books: one virtual account per agent inside the shared
paper money. Cash moves only through fills; positions keep an average cost
(per share, or per option share - the quoted premium); a sell realizes P&L
against that average.

Separate from the rule-based agents' shadow_positions on purpose: those are
0DTE-only, managed by the worker's exit rules. These hold stocks and options of
any expiry, for as long as the agent decides.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_accounts (
    agent TEXT PRIMARY KEY,
    starting_balance REAL NOT NULL,
    cash REAL NOT NULL,
    peak_equity REAL NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    instrument TEXT NOT NULL,         -- 'TSLA' or 'TSLA 2026-12-18 400C'
    asset TEXT NOT NULL,              -- stock | option
    symbol TEXT NOT NULL,
    option_type TEXT,
    strike REAL,
    expiration TEXT,
    qty INTEGER NOT NULL,             -- shares or contracts still held
    avg_cost REAL NOT NULL,           -- per share / per option share
    multiplier INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    current_price REAL,               -- last mark (bid side for options)
    marked_at TEXT,
    realized_pnl REAL NOT NULL DEFAULT 0,   -- net of fees, over the position's life
    fees REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',    -- open | closed
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_positions ON agent_positions(agent, status);

CREATE TABLE IF NOT EXISTS agent_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    position_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    side TEXT NOT NULL,               -- buy | sell | expire
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    fees REAL NOT NULL,
    venue TEXT NOT NULL,              -- sim | alpaca | expiry
    venue_order_id TEXT,
    reason TEXT,
    realized REAL                     -- P&L this fill realized (sells/expiries), net of its fees
);
CREATE INDEX IF NOT EXISTS idx_agent_fills ON agent_fills(agent, at);

CREATE TABLE IF NOT EXISTS agent_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    at TEXT NOT NULL,
    rule TEXT NOT NULL,
    detail TEXT,
    order_json TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0   -- shown to the agent at its next wake-up
);

CREATE TABLE IF NOT EXISTS agent_daily (
    agent TEXT NOT NULL,
    date TEXT NOT NULL,               -- exchange-time date
    start_equity REAL NOT NULL,       -- base of the daily loss limit
    PRIMARY KEY (agent, date)
);
"""


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_fills)")}
    if "realized" not in cols:          # added after the table first shipped
        conn.execute("ALTER TABLE agent_fills ADD COLUMN realized REAL")


def _connect(path):
    from storage.db import BUSY_TIMEOUT_SECONDS
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- accounts -----------------------------------------------------------------

def ensure_account(path: str | Path, agent: str, starting_balance: float) -> None:
    conn = _connect(path)
    try:
        conn.execute("INSERT OR IGNORE INTO agent_accounts (agent, starting_balance, cash, "
                     "peak_equity, created_at) VALUES (?,?,?,?,?)",
                     (agent, starting_balance, starting_balance, starting_balance, _now()))
        conn.commit()
    finally:
        conn.close()


def get_account(path: str | Path, agent: str) -> sqlite3.Row | None:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_accounts WHERE agent = ?", (agent,)).fetchone()
    finally:
        conn.close()


def list_accounts(path: str | Path) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_accounts ORDER BY agent").fetchall()
    finally:
        conn.close()


def set_paused(path: str | Path, agent: str, paused: bool, reason: str | None = None) -> None:
    conn = _connect(path)
    try:
        conn.execute("UPDATE agent_accounts SET paused = ?, pause_reason = ? WHERE agent = ?",
                     (int(paused), reason if paused else None, agent))
        conn.commit()
    finally:
        conn.close()


def update_peak(path: str | Path, agent: str, equity: float) -> float:
    conn = _connect(path)
    try:
        conn.execute("UPDATE agent_accounts SET peak_equity = MAX(peak_equity, ?) WHERE agent = ?",
                     (equity, agent))
        conn.commit()
        return conn.execute("SELECT peak_equity FROM agent_accounts WHERE agent = ?",
                            (agent,)).fetchone()["peak_equity"]
    finally:
        conn.close()


def day_start_equity(path: str | Path, agent: str, day: str, equity_now: float) -> float:
    """The equity this agent started `day` with; the first call of the day
    records it."""
    conn = _connect(path)
    try:
        conn.execute("INSERT OR IGNORE INTO agent_daily (agent, date, start_equity) VALUES (?,?,?)",
                     (agent, day, equity_now))
        conn.commit()
        return conn.execute("SELECT start_equity FROM agent_daily WHERE agent = ? AND date = ?",
                            (agent, day)).fetchone()["start_equity"]
    finally:
        conn.close()


# --- positions ----------------------------------------------------------------

def open_positions(path: str | Path, agent: str | None = None) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        if agent:
            return conn.execute("SELECT * FROM agent_positions WHERE status = 'open' AND agent = ? "
                                "ORDER BY id", (agent,)).fetchall()
        return conn.execute("SELECT * FROM agent_positions WHERE status = 'open' ORDER BY id").fetchall()
    finally:
        conn.close()


def closed_positions(path: str | Path, agent: str, limit: int = 200) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_positions WHERE status = 'closed' AND agent = ? "
                            "ORDER BY closed_at DESC LIMIT ?", (agent, limit)).fetchall()
    finally:
        conn.close()


def find_open(path: str | Path, agent: str, instrument: str) -> sqlite3.Row | None:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_positions WHERE agent = ? AND instrument = ? "
                            "AND status = 'open'", (agent, instrument)).fetchone()
    finally:
        conn.close()


def mark(path: str | Path, position_id: int, price: float) -> None:
    conn = _connect(path)
    try:
        conn.execute("UPDATE agent_positions SET current_price = ?, marked_at = ? WHERE id = ?",
                     (price, _now(), position_id))
        conn.commit()
    finally:
        conn.close()


def record_fill(path: str | Path, *, agent: str, instrument: str, asset: str, symbol: str,
                option_type: str | None, strike: float | None, expiration: str | None,
                multiplier: int, side: str, qty: int, price: float, fees: float, venue: str,
                venue_order_id: str | None = None, reason: str | None = None) -> dict:
    """Apply one fill to the agent's cash and position, in one transaction.
    'buy' opens or adds (average cost); 'sell'/'expire' reduce and realize P&L.
    Returns {'position_id', 'realized', 'cash'}. The caller (the broker
    service) has already checked the agent can afford it / holds the qty."""
    now = _now()
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        pos = conn.execute("SELECT * FROM agent_positions WHERE agent = ? AND instrument = ? "
                           "AND status = 'open'", (agent, instrument)).fetchone()
        realized = 0.0
        if side == "buy":
            cash_delta = -(price * qty * multiplier + fees)
            if pos:
                new_qty = pos["qty"] + qty
                avg = (pos["avg_cost"] * pos["qty"] + price * qty) / new_qty
                conn.execute("UPDATE agent_positions SET qty = ?, avg_cost = ?, fees = fees + ?, "
                             "realized_pnl = realized_pnl - ? WHERE id = ?",
                             (new_qty, avg, fees, fees, pos["id"]))
                pid = pos["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO agent_positions (agent, instrument, asset, symbol, option_type, "
                    "strike, expiration, qty, avg_cost, multiplier, opened_at, current_price, "
                    "marked_at, realized_pnl, fees) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (agent, instrument, asset, symbol, option_type, strike, expiration, qty, price,
                     multiplier, now, price, now, -fees, fees))
                pid = cur.lastrowid
            realized = -fees
        else:
            if not pos or pos["qty"] < qty:
                raise ValueError(f"{agent} holds {pos['qty'] if pos else 0} of {instrument}, can't sell {qty}")
            cash_delta = price * qty * multiplier - fees
            realized = (price - pos["avg_cost"]) * qty * multiplier - fees
            left = pos["qty"] - qty
            conn.execute("UPDATE agent_positions SET qty = ?, fees = fees + ?, "
                         "realized_pnl = realized_pnl + ?, current_price = ?, marked_at = ?, "
                         "status = ?, closed_at = ? WHERE id = ?",
                         (left, fees, realized, price, now, "open" if left else "closed",
                          None if left else now, pos["id"]))
            pid = pos["id"]
        conn.execute("UPDATE agent_accounts SET cash = cash + ? WHERE agent = ?", (cash_delta, agent))
        conn.execute("INSERT INTO agent_fills (agent, position_id, at, side, qty, price, fees, venue, "
                     "venue_order_id, reason, realized) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (agent, pid, now, side, qty, price, fees, venue, venue_order_id, reason,
                      None if side == "buy" else realized))
        cash = conn.execute("SELECT cash FROM agent_accounts WHERE agent = ?", (agent,)).fetchone()["cash"]
        conn.commit()
        return {"position_id": pid, "realized": realized, "cash": cash}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fills(path: str | Path, agent: str, limit: int = 100) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_fills WHERE agent = ? ORDER BY id DESC LIMIT ?",
                            (agent, limit)).fetchall()
    finally:
        conn.close()


# --- rejections ---------------------------------------------------------------

def record_rejection(path: str | Path, agent: str, rule: str, detail: str | None, order: dict) -> None:
    conn = _connect(path)
    try:
        conn.execute("INSERT INTO agent_rejections (agent, at, rule, detail, order_json) VALUES (?,?,?,?,?)",
                     (agent, _now(), rule, detail, json.dumps(order)))
        conn.commit()
    finally:
        conn.close()


def unseen_rejections(path: str | Path, agent: str, mark_seen: bool = True) -> list[dict]:
    conn = _connect(path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM agent_rejections WHERE agent = ? AND seen = 0 ORDER BY id", (agent,))]
        if mark_seen and rows:
            conn.execute("UPDATE agent_rejections SET seen = 1 WHERE agent = ? AND seen = 0", (agent,))
            conn.commit()
        return rows
    finally:
        conn.close()


def recent_rejections(path: str | Path, agent: str, limit: int = 10) -> list[sqlite3.Row]:
    conn = _connect(path)
    try:
        return conn.execute("SELECT * FROM agent_rejections WHERE agent = ? ORDER BY id DESC LIMIT ?",
                            (agent, limit)).fetchall()
    finally:
        conn.close()
