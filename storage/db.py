"""SQLite schema and data access for signal snapshots, positions, and the paper account.

The worker process writes here; the dashboard only reads. A "trade" is just a
position with status='closed' - no separate trade table, since a closed
position already carries entry/exit price and realized P&L.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    composite_score REAL NOT NULL,
    recommendation TEXT NOT NULL,
    subscores_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_ticker_time
    ON signal_snapshots (ticker, timestamp DESC);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    option_type TEXT NOT NULL,          -- 'call' or 'put'
    strike REAL NOT NULL,
    expiration TEXT NOT NULL,
    contracts INTEGER NOT NULL,
    entry_price REAL NOT NULL,          -- premium per contract at entry
    cost_basis REAL NOT NULL,           -- entry_price * contracts * 100
    entry_time TEXT NOT NULL,
    entry_composite_score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',-- 'open' or 'closed'
    exit_price REAL,
    exit_time TEXT,
    exit_reason TEXT,                   -- 'profit_target' | 'stop_loss' | 'signal_reversal' | 'time_cutoff' | 'manual'
    pnl REAL,
    suggested_exit_reason TEXT,         -- set while open, once an exit condition fires
    current_price REAL                  -- last price seen by the worker, for dashboard-side unrealized P&L
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (status);
"""


def init_db(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def connect(path: str | Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- account -----------------------------------------------------------

def ensure_account(db_path: str | Path, starting_balance: float) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM account WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO account (id, balance, updated_at) VALUES (1, ?, ?)",
                (starting_balance, _now()),
            )


def get_balance(db_path: str | Path) -> float:
    with connect(db_path) as conn:
        row = conn.execute("SELECT balance FROM account WHERE id = 1").fetchone()
        return row["balance"] if row else 0.0


def set_balance(db_path: str | Path, balance: float) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE account SET balance = ?, updated_at = ? WHERE id = 1",
            (balance, _now()),
        )


# --- signal snapshots ----------------------------------------------------

def insert_signal_snapshot(
    db_path: str | Path,
    ticker: str,
    direction: str,
    confidence: float,
    composite_score: float,
    recommendation: str,
    subscores: dict,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signal_snapshots
               (ticker, timestamp, direction, confidence, composite_score,
                recommendation, subscores_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, _now(), direction, confidence, composite_score,
             recommendation, json.dumps(subscores)),
        )


def get_latest_signal(db_path: str | Path, ticker: str) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            """SELECT * FROM signal_snapshots
               WHERE ticker = ? ORDER BY timestamp DESC LIMIT 1""",
            (ticker,),
        ).fetchone()


# --- positions -------------------------------------------------------------

def open_position(
    db_path: str | Path,
    ticker: str,
    option_type: str,
    strike: float,
    expiration: str,
    contracts: int,
    entry_price: float,
    entry_composite_score: float,
) -> int:
    cost_basis = entry_price * contracts * 100
    with connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (ticker, option_type, strike, expiration, contracts, entry_price,
                cost_basis, entry_time, entry_composite_score, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (ticker, option_type, strike, expiration, contracts, entry_price,
             cost_basis, _now(), entry_composite_score),
        )
        return cur.lastrowid


def get_open_positions(db_path: str | Path) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_time"
        ).fetchall()


def get_closed_positions(db_path: str | Path) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM positions WHERE status = 'closed' ORDER BY exit_time DESC"
        ).fetchall()


def flag_suggested_exit(db_path: str | Path, position_id: int, reason: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE positions SET suggested_exit_reason = ? WHERE id = ?",
            (reason, position_id),
        )


def update_position_price(db_path: str | Path, position_id: int, current_price: float) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE positions SET current_price = ? WHERE id = ?",
            (current_price, position_id),
        )


def close_position(
    db_path: str | Path, position_id: int, exit_price: float, exit_reason: str
) -> float:
    """Closes a position, realizes P&L, and returns the P&L (caller updates balance)."""
    with connect(db_path) as conn:
        pos = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        proceeds = exit_price * pos["contracts"] * 100
        pnl = proceeds - pos["cost_basis"]
        conn.execute(
            """UPDATE positions
               SET status = 'closed', exit_price = ?, exit_time = ?,
                   exit_reason = ?, pnl = ?, suggested_exit_reason = NULL
               WHERE id = ?""",
            (exit_price, _now(), exit_reason, pnl, position_id),
        )
        return pnl
