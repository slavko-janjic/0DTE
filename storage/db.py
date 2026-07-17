"""SQLite schema and data access for signal snapshots, positions, and the paper account.

The worker process writes here; the dashboard only reads. A "trade" is just a
position with status='closed' - no separate trade table, since a closed
position already carries entry/exit price and realized P&L.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# A direction "streak" only continues across consecutive polls. Anything longer
# than this gap (the overnight/weekend break, or the worker being down) starts a
# fresh run rather than pretending the signal held the whole time.
STREAK_MAX_GAP_MINUTES = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    poll_interval_seconds INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weight_overrides (
    ticker TEXT PRIMARY KEY,
    weights_json TEXT NOT NULL,
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
    subscores_json TEXT NOT NULL,
    spot_price REAL
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
    current_price REAL,                 -- last price seen by the worker, for dashboard-side unrealized P&L
    profit_target_pct REAL,             -- per-trade auto-exit at this % gain (NULL = off)
    stop_loss_pct REAL,                 -- per-trade auto-exit at this % loss, negative (NULL = off)
    opened_by TEXT NOT NULL DEFAULT 'manual'  -- 'manual' (dashboard buy) or 'auto' (auto-pilot)
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (status);

CREATE TABLE IF NOT EXISTS autopilot_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL,               -- legacy; superseded by mode (kept for migration)
    mode TEXT NOT NULL DEFAULT 'off',       -- 'off' | 'continuous' | 'day'
    armed_date TEXT,                        -- ISO date the 'day' session is armed for
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL,
    last_run_date TEXT,                     -- ISO date of the last completed calibration pass
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_inversions (
    ticker TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ticker, category)
);

CREATE TABLE IF NOT EXISTS confidence_bands (
    ticker TEXT PRIMARY KEY,
    bands_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL,        -- weights_nudged | inversion_added | inversion_removed | confidence_map_updated | reverted
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_setups (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,        -- ISO date, market tz
    setup_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);

-- Shadow strategy lab: virtual positions traded by named strategies running
-- alongside the real paper account. Zero balance impact - pure bookkeeping so
-- N strategies can build win/loss track records from the same market data.
CREATE TABLE IF NOT EXISTS shadow_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    ticker TEXT NOT NULL,
    option_type TEXT NOT NULL,          -- 'call' or 'put'
    strike REAL NOT NULL,
    expiration TEXT NOT NULL,
    contracts INTEGER NOT NULL DEFAULT 1,
    entry_price REAL NOT NULL,          -- ask-side honest fill
    entry_time TEXT NOT NULL,
    entry_reason_json TEXT NOT NULL,    -- audit: confidence/gamma/subscores at entry
    status TEXT NOT NULL DEFAULT 'open',
    current_price REAL,
    max_price REAL,                     -- high-water mark, for trailing stops
    exit_price REAL,                    -- bid-side honest fill
    exit_time TEXT,
    exit_reason TEXT,
    pnl REAL,
    pnl_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_shadow_positions_strategy_status
    ON shadow_positions (strategy, status);

-- The cost of transacting, logged every cycle. Direction is unpredictable but
-- the bid/ask spread is a KNOWN, mechanical toll paid on every round trip, and
-- it follows a structural intraday pattern (wide at the open, tightest
-- mid-morning, blowing out into the close). Tracking it turns the one certain
-- cost from an unknown into something we can time around.
CREATE TABLE IF NOT EXISTS quote_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    option_type TEXT NOT NULL,      -- 'call' or 'put' (the ATM contract)
    strike REAL NOT NULL,
    spot REAL,
    bid REAL,
    ask REAL,
    mid REAL,
    spread_pct REAL,                -- (ask-bid)/mid * 100: the round-trip toll
    minutes_since_open REAL         -- so the intraday cost curve needs no tz math later
);
CREATE INDEX IF NOT EXISTS idx_quote_snapshots_ticker_time
    ON quote_snapshots (ticker, timestamp DESC);

-- The headlines behind the trump_news score, one row per cycle (market-wide,
-- so not duplicated per ticker). Without these a score jumping 0.1 -> 0.8 is
-- unexplainable: you can see that something moved the needle but never what.
-- Stored so an event can be traced back to the actual words.
CREATE TABLE IF NOT EXISTS news_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    score REAL,                     -- the trump_news subscore this produced
    headline_count INTEGER,
    headlines_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_snapshots_time
    ON news_snapshots (timestamp DESC);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Adds columns introduced after initial release to pre-existing databases."""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(signal_snapshots)")}
    if "spot_price" not in existing_columns:
        conn.execute("ALTER TABLE signal_snapshots ADD COLUMN spot_price REAL")

    position_columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    if "profit_target_pct" not in position_columns:
        conn.execute("ALTER TABLE positions ADD COLUMN profit_target_pct REAL")
    if "stop_loss_pct" not in position_columns:
        conn.execute("ALTER TABLE positions ADD COLUMN stop_loss_pct REAL")
    if "opened_by" not in position_columns:
        conn.execute("ALTER TABLE positions ADD COLUMN opened_by TEXT NOT NULL DEFAULT 'manual'")
    if "max_price" not in position_columns:
        # high-water mark of the premium since entry, for the trailing stop.
        # Backfill existing open rows to their entry_price so an in-flight
        # position doesn't arm a trailing stop off a NULL peak.
        conn.execute("ALTER TABLE positions ADD COLUMN max_price REAL")
        conn.execute("UPDATE positions SET max_price = entry_price WHERE max_price IS NULL")

    # weight_overrides went from a single global row (id CHECK(id=1)) to per-ticker
    # (ticker PRIMARY KEY). Recreate the table if it still has the old shape - an
    # active override is just a regenerable tuning choice, safe to drop.
    wo_columns = {row[1] for row in conn.execute("PRAGMA table_info(weight_overrides)")}
    if wo_columns and "ticker" not in wo_columns:
        conn.execute("DROP TABLE weight_overrides")
        conn.execute(
            "CREATE TABLE weight_overrides (ticker TEXT PRIMARY KEY, "
            "weights_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    # autopilot went from a plain on/off flag to mode ('off'|'continuous'|'day') +
    # armed_date; backfill enabled=1 as 'continuous' so an armed autopilot stays on.
    ap_columns = {row[1] for row in conn.execute("PRAGMA table_info(autopilot_settings)")}
    if ap_columns and "mode" not in ap_columns:
        conn.execute("ALTER TABLE autopilot_settings ADD COLUMN mode TEXT NOT NULL DEFAULT 'off'")
        conn.execute("ALTER TABLE autopilot_settings ADD COLUMN armed_date TEXT")
        conn.execute("UPDATE autopilot_settings SET mode = 'continuous' WHERE enabled = 1")

    if "calibrated_confidence" not in existing_columns:
        conn.execute("ALTER TABLE signal_snapshots ADD COLUMN calibrated_confidence REAL")
    if "gamma_score" not in existing_columns:
        conn.execute("ALTER TABLE signal_snapshots ADD COLUMN gamma_score REAL")
    if "gamma_regime" not in existing_columns:
        conn.execute("ALTER TABLE signal_snapshots ADD COLUMN gamma_regime TEXT")
    if "direction_streak" not in existing_columns:
        conn.execute("ALTER TABLE signal_snapshots ADD COLUMN direction_streak INTEGER")
        _backfill_direction_streaks(conn)


def _backfill_direction_streaks(conn: sqlite3.Connection) -> None:
    """One-time: compute direction_streak across pre-existing snapshots so the
    persistence analysis has the full accumulated history immediately instead of
    starting from zero. Runs only when the column is first added (and is
    naturally idempotent - it can't run twice)."""
    rows = conn.execute(
        "SELECT id, ticker, timestamp, direction FROM signal_snapshots ORDER BY ticker, timestamp"
    ).fetchall()
    updates = []
    prev_ticker = prev_direction = prev_ts = None
    streak = 0
    for row_id, ticker, ts_text, direction in rows:
        ts = datetime.fromisoformat(ts_text)
        continues = (
            ticker == prev_ticker
            and direction == prev_direction
            and prev_ts is not None
            and (ts - prev_ts) <= timedelta(minutes=STREAK_MAX_GAP_MINUTES)
        )
        streak = streak + 1 if continues else 1
        updates.append((streak, row_id))
        prev_ticker, prev_direction, prev_ts = ticker, direction, ts
    conn.executemany(
        "UPDATE signal_snapshots SET direction_streak = ? WHERE id = ?", updates
    )


def init_db(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()


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


def backup_db(db_path: str | Path, backup_dir: str | Path, keep: int = 14) -> Path | None:
    """Writes a dated snapshot of the database into backup_dir (skipping if
    today's already exists) and prunes to the newest `keep` files. Uses SQLite's
    online backup API, so it's safe while the worker holds the DB open.
    Returns the backup path, or None if today's backup already existed."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"0dte_{datetime.now().date().isoformat()}.db"
    if target.exists():
        return None

    with sqlite3.connect(db_path) as source, sqlite3.connect(target) as dest:
        source.backup(dest)

    backups = sorted(backup_dir.glob("0dte_*.db"))
    for stale in backups[:-keep]:
        stale.unlink()
    return target


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


# --- worker settings -------------------------------------------------------

def ensure_worker_settings(db_path: str | Path, default_poll_interval_seconds: int) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM worker_settings WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO worker_settings (id, poll_interval_seconds, updated_at) VALUES (1, ?, ?)",
                (default_poll_interval_seconds, _now()),
            )


def get_poll_interval_seconds(db_path: str | Path) -> int:
    with connect(db_path) as conn:
        row = conn.execute("SELECT poll_interval_seconds FROM worker_settings WHERE id = 1").fetchone()
        return row["poll_interval_seconds"] if row else 300


def set_poll_interval_seconds(db_path: str | Path, seconds: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE worker_settings SET poll_interval_seconds = ?, updated_at = ? WHERE id = 1",
            (seconds, _now()),
        )


# --- autopilot ---------------------------------------------------------------

def ensure_autopilot(db_path: str | Path, default_enabled: bool = False) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM autopilot_settings WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO autopilot_settings (id, enabled, mode, updated_at)
                   VALUES (1, ?, ?, ?)""",
                (int(default_enabled), "continuous" if default_enabled else "off", _now()),
            )


def get_autopilot_state(db_path: str | Path) -> tuple[str, str | None]:
    """Returns (mode, armed_date): mode is 'off' | 'continuous' | 'day';
    armed_date is the ISO date a 'day' session is armed for (else None)."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT mode, armed_date FROM autopilot_settings WHERE id = 1"
        ).fetchone()
        if row is None:
            return "off", None
        return row["mode"], row["armed_date"]


def set_autopilot_state(db_path: str | Path, mode: str, armed_date: str | None = None) -> None:
    if mode not in ("off", "continuous", "day"):
        raise ValueError(f"invalid autopilot mode: {mode}")
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO autopilot_settings (id, enabled, mode, armed_date, updated_at)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled,
                                             mode = excluded.mode,
                                             armed_date = excluded.armed_date,
                                             updated_at = excluded.updated_at""",
            (int(mode != "off"), mode, armed_date, _now()),
        )


def get_autopilot_enabled(db_path: str | Path, tz_name: str = "America/New_York") -> bool:
    """Whether autopilot should trade right now: 'continuous' is always active;
    'day' is active only on its armed date (in the market timezone)."""
    mode, armed_date = get_autopilot_state(db_path)
    if mode == "continuous":
        return True
    if mode == "day":
        today = datetime.now(ZoneInfo(tz_name)).date().isoformat()
        return armed_date == today
    return False


# --- self-calibration --------------------------------------------------------

def ensure_calibration(db_path: str | Path, default_enabled: bool = True) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM calibration_settings WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO calibration_settings (id, enabled, updated_at) VALUES (1, ?, ?)",
                (int(default_enabled), _now()),
            )


def get_calibration_enabled(db_path: str | Path) -> bool:
    with connect(db_path) as conn:
        row = conn.execute("SELECT enabled FROM calibration_settings WHERE id = 1").fetchone()
        return bool(row["enabled"]) if row is not None else False


def set_calibration_enabled(db_path: str | Path, enabled: bool) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO calibration_settings (id, enabled, updated_at) VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled,
                                             updated_at = excluded.updated_at""",
            (int(enabled), _now()),
        )


def get_last_calibration_date(db_path: str | Path) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_run_date FROM calibration_settings WHERE id = 1"
        ).fetchone()
        return row["last_run_date"] if row is not None else None


def set_last_calibration_date(db_path: str | Path, iso_date: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE calibration_settings SET last_run_date = ?, updated_at = ? WHERE id = 1",
            (iso_date, _now()),
        )


def get_inversions(db_path: str | Path, ticker: str) -> list[str]:
    """Auto-managed (calibration-added) per-ticker signal inversions."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT category FROM signal_inversions WHERE ticker = ? ORDER BY category",
            (ticker,),
        ).fetchall()
        return [r["category"] for r in rows]


def add_inversion(db_path: str | Path, ticker: str, category: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signal_inversions (ticker, category, created_at) VALUES (?, ?, ?)
               ON CONFLICT(ticker, category) DO NOTHING""",
            (ticker, category, _now()),
        )


def remove_inversion(db_path: str | Path, ticker: str, category: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM signal_inversions WHERE ticker = ? AND category = ?",
            (ticker, category),
        )


def effective_inversions(db_path: str | Path, config: dict, ticker: str) -> list[str]:
    """The inversions actually applied to a ticker: the manual global list from
    config/settings.yaml plus any auto-calibration-added per-ticker ones."""
    manual = config.get("invert_categories", []) or []
    return sorted(set(manual) | set(get_inversions(db_path, ticker)))


def get_confidence_bands(db_path: str | Path, ticker: str) -> list[dict] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT bands_json FROM confidence_bands WHERE ticker = ?", (ticker,)
        ).fetchone()
        return json.loads(row["bands_json"]) if row is not None else None


def set_confidence_bands(db_path: str | Path, ticker: str, bands: list[dict]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO confidence_bands (ticker, bands_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET bands_json = excluded.bands_json,
                                                 updated_at = excluded.updated_at""",
            (ticker, json.dumps(bands), _now()),
        )


def clear_confidence_bands(db_path: str | Path, ticker: str) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM confidence_bands WHERE ticker = ?", (ticker,))


def log_calibration_event(db_path: str | Path, ticker: str, kind: str, detail: dict) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO calibration_events (ticker, kind, detail_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (ticker, kind, json.dumps(detail), _now()),
        )


def get_calibration_events(db_path: str | Path, limit: int = 50) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM calibration_events ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()


# --- pre-market day setups (per ticker, per day) ----------------------------

def set_day_setup(db_path: str | Path, ticker: str, date: str, setup: dict) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO day_setups (ticker, date, setup_json, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(ticker, date) DO UPDATE SET setup_json = excluded.setup_json,
                                                       created_at = excluded.created_at""",
            (ticker, date, json.dumps(setup), _now()),
        )


def get_day_setup(db_path: str | Path, ticker: str, date: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT setup_json FROM day_setups WHERE ticker = ? AND date = ?", (ticker, date)
        ).fetchone()
        return json.loads(row["setup_json"]) if row is not None else None


# --- news snapshots (what was actually said) --------------------------------

def insert_news_snapshot(db_path: str | Path, headlines: list[str] | None,
                         score: float | None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO news_snapshots (timestamp, score, headline_count, headlines_json)
               VALUES (?, ?, ?, ?)""",
            (_now(), score, len(headlines or []), json.dumps(headlines or [])),
        )


def get_news_history(db_path: str | Path, limit: int = 5000) -> list[sqlite3.Row]:
    """Oldest-first news history, for explaining a trump_news score move."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM (SELECT * FROM news_snapshots ORDER BY timestamp DESC "
            "LIMIT ?) ORDER BY timestamp ASC", (limit,)
        ).fetchall()


def get_news_at(db_path: str | Path, timestamp_iso: str) -> sqlite3.Row | None:
    """The headlines in force at a moment - the newest snapshot at or before it.
    Used to answer 'what was said that moved this?'."""
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM news_snapshots WHERE timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT 1", (timestamp_iso,)
        ).fetchone()


# --- quote snapshots (the cost of transacting) ------------------------------

def insert_quote_snapshot(
    db_path: str | Path, ticker: str, option_type: str, strike: float,
    spot: float | None, bid: float | None, ask: float | None,
    mid: float | None, spread_pct: float | None, minutes_since_open: float | None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO quote_snapshots
               (ticker, timestamp, option_type, strike, spot, bid, ask, mid,
                spread_pct, minutes_since_open)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, _now(), option_type, strike, spot, bid, ask, mid,
             spread_pct, minutes_since_open),
        )


def get_quote_history(db_path: str | Path, ticker: str | None = None,
                      limit: int = 20000) -> list[sqlite3.Row]:
    """Oldest-first quote history for the intraday cost curve."""
    with connect(db_path) as conn:
        if ticker is None:
            return conn.execute(
                "SELECT * FROM (SELECT * FROM quote_snapshots ORDER BY timestamp DESC "
                "LIMIT ?) ORDER BY timestamp ASC", (limit,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM (SELECT * FROM quote_snapshots WHERE ticker = ? "
            "ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC", (ticker, limit)
        ).fetchall()


def get_latest_quote(db_path: str | Path, ticker: str) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM quote_snapshots WHERE ticker = ? ORDER BY timestamp DESC LIMIT 1",
            (ticker,),
        ).fetchone()


# --- shadow strategy positions (virtual book, no balance impact) ------------

def open_shadow_position(
    db_path: str | Path, strategy: str, ticker: str, option_type: str,
    strike: float, expiration: str, entry_price: float, entry_reason: dict,
    contracts: int = 1,
) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO shadow_positions
               (strategy, ticker, option_type, strike, expiration, contracts,
                entry_price, entry_time, entry_reason_json, status, max_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (strategy, ticker, option_type, strike, expiration, contracts,
             entry_price, _now(), json.dumps(entry_reason), entry_price),
        )
        return cur.lastrowid


def get_open_shadow_positions(db_path: str | Path, ticker: str | None = None) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        if ticker is None:
            return conn.execute(
                "SELECT * FROM shadow_positions WHERE status = 'open' ORDER BY entry_time"
            ).fetchall()
        return conn.execute(
            "SELECT * FROM shadow_positions WHERE status = 'open' AND ticker = ? ORDER BY entry_time",
            (ticker,),
        ).fetchall()


def get_closed_shadow_positions(db_path: str | Path, limit: int = 2000) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT * FROM shadow_positions WHERE status = 'closed' "
            "ORDER BY exit_time DESC LIMIT ?",
            (limit,),
        ).fetchall()


def update_shadow_price(db_path: str | Path, position_id: int, current_price: float) -> None:
    """Latest premium + high-water mark bump, mirroring update_position_price."""
    with connect(db_path) as conn:
        conn.execute(
            """UPDATE shadow_positions
               SET current_price = ?,
                   max_price = MAX(COALESCE(max_price, ?), ?)
               WHERE id = ?""",
            (current_price, current_price, current_price, position_id),
        )


def close_shadow_position(
    db_path: str | Path, position_id: int, exit_price: float, reason: str,
) -> float | None:
    """Books the virtual exit. Returns realized pnl dollars, or None if the row
    was already closed (same status-guard idempotency as the real close)."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT entry_price, contracts FROM shadow_positions WHERE id = ? AND status = 'open'",
            (position_id,),
        ).fetchone()
        if row is None:
            return None
        pnl = (exit_price - row["entry_price"]) * row["contracts"] * 100
        pnl_pct = ((exit_price - row["entry_price"]) / row["entry_price"] * 100.0
                   if row["entry_price"] else 0.0)
        cur = conn.execute(
            """UPDATE shadow_positions
               SET status = 'closed', exit_price = ?, exit_time = ?, exit_reason = ?,
                   pnl = ?, pnl_pct = ?
               WHERE id = ? AND status = 'open'""",
            (exit_price, _now(), reason, pnl, pnl_pct, position_id),
        )
        return pnl if cur.rowcount else None


def count_shadow_entries_today(
    db_path: str | Path, strategy: str, ticker: str, today_iso: str,
) -> int:
    """Shadow entries (open or closed) a strategy has made in a ticker today -
    date prefix match on the ISO UTC entry_time is close enough for the per-day
    entry cap."""
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM shadow_positions
               WHERE strategy = ? AND ticker = ? AND entry_time >= ?""",
            (strategy, ticker, today_iso),
        ).fetchone()
        return row["n"]


# --- weight overrides -------------------------------------------------------

def get_weight_overrides(db_path: str | Path, ticker: str) -> tuple[dict, str] | None:
    """Returns (weights, updated_at_iso) if an override is active for this ticker,
    else None. Overrides are per-ticker."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT weights_json, updated_at FROM weight_overrides WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["weights_json"]), row["updated_at"]


def set_weight_overrides(db_path: str | Path, ticker: str, weights: dict) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO weight_overrides (ticker, weights_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET weights_json = excluded.weights_json,
                                                 updated_at = excluded.updated_at""",
            (ticker, json.dumps(weights), _now()),
        )


def clear_weight_overrides(db_path: str | Path, ticker: str) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM weight_overrides WHERE ticker = ?", (ticker,))


def effective_weights(db_path: str | Path, config: dict, ticker: str) -> dict:
    """The weights actually in use for a ticker: a dashboard-applied per-ticker DB
    override if one is active, otherwise the config/settings.yaml defaults."""
    override = get_weight_overrides(db_path, ticker)
    return override[0] if override is not None else config["weights"]


# --- signal snapshots ----------------------------------------------------

def insert_signal_snapshot(
    db_path: str | Path,
    ticker: str,
    direction: str,
    confidence: float,
    composite_score: float,
    recommendation: str,
    subscores: dict,
    spot_price: float | None = None,
    calibrated_confidence: float | None = None,
    gamma_score: float | None = None,
    gamma_regime: str | None = None,
    direction_streak: int | None = None,
) -> None:
    """confidence is the RAW |composite|*100 value - all accuracy grading and
    calibration math keys off it. calibrated_confidence is the corrected
    display/decision value (None when no calibration map exists yet). gamma_score
    (-1..1) and gamma_regime ('positive'|'negative'|'neutral') describe the
    dealer-gamma regime for that cycle - a rangebound-vs-trending hint, not a
    directional call. direction_streak is how many consecutive polls this
    direction has held (see next_direction_streak)."""
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signal_snapshots
               (ticker, timestamp, direction, confidence, composite_score,
                recommendation, subscores_json, spot_price, calibrated_confidence,
                gamma_score, gamma_regime, direction_streak)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, _now(), direction, confidence, composite_score,
             recommendation, json.dumps(subscores), spot_price, calibrated_confidence,
             gamma_score, gamma_regime, direction_streak),
        )


def next_direction_streak(db_path: str | Path, ticker: str, direction: str) -> int:
    """How long `direction` will have held once the current cycle is stored: the
    previous snapshot's streak + 1 when the direction is unchanged and the poll
    was recent, else 1. O(1) - reads only the latest snapshot."""
    previous = get_latest_signal(db_path, ticker)
    if previous is None or previous["direction"] != direction:
        return 1
    gap = datetime.now(timezone.utc) - datetime.fromisoformat(previous["timestamp"])
    if gap > timedelta(minutes=STREAK_MAX_GAP_MINUTES):
        return 1  # overnight/weekend break or a worker outage - fresh run
    return (previous["direction_streak"] or 0) + 1


def get_latest_signal(db_path: str | Path, ticker: str) -> sqlite3.Row | None:
    with connect(db_path) as conn:
        return conn.execute(
            """SELECT * FROM signal_snapshots
               WHERE ticker = ? ORDER BY timestamp DESC LIMIT 1""",
            (ticker,),
        ).fetchone()


def get_signal_history(db_path: str | Path, ticker: str, limit: int = 2000) -> list[sqlite3.Row]:
    """Oldest-first signal history for charting/accuracy tracking."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM (
                   SELECT * FROM signal_snapshots WHERE ticker = ?
                   ORDER BY timestamp DESC LIMIT ?
               ) ORDER BY timestamp ASC""",
            (ticker, limit),
        ).fetchall()
        return rows


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
    opened_by: str = "manual",
) -> int:
    cost_basis = entry_price * contracts * 100
    with connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (ticker, option_type, strike, expiration, contracts, entry_price,
                cost_basis, entry_time, entry_composite_score, status, opened_by, max_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
            (ticker, option_type, strike, expiration, contracts, entry_price,
             cost_basis, _now(), entry_composite_score, opened_by, entry_price),
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


def clear_closed_positions(db_path: str | Path) -> None:
    """Deletes closed trade records only - open positions and account balance are untouched."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM positions WHERE status = 'closed'")


def flag_suggested_exit(db_path: str | Path, position_id: int, reason: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE positions SET suggested_exit_reason = ? WHERE id = ?",
            (reason, position_id),
        )


def update_position_price(db_path: str | Path, position_id: int, current_price: float) -> None:
    """Stores the latest premium and raises the high-water mark (max_price) used
    by the trailing stop. COALESCE guards a legacy row with a NULL peak."""
    with connect(db_path) as conn:
        conn.execute(
            """UPDATE positions
               SET current_price = ?,
                   max_price = MAX(COALESCE(max_price, ?), ?)
               WHERE id = ?""",
            (current_price, current_price, current_price, position_id),
        )


def set_position_exit_targets(
    db_path: str | Path, position_id: int,
    profit_target_pct: float | None, stop_loss_pct: float | None,
) -> None:
    """Sets a position's per-trade profit target (positive %) and stop loss
    (negative %). Either may be None to leave that trigger off."""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE positions SET profit_target_pct = ?, stop_loss_pct = ? WHERE id = ?",
            (profit_target_pct, stop_loss_pct, position_id),
        )


def close_position(
    db_path: str | Path, position_id: int, exit_price: float, exit_reason: str
) -> float | None:
    """Closes an OPEN position, realizes P&L, and returns the P&L (caller updates
    balance). Returns None if the position was already closed - the guarded UPDATE
    makes this safe against two closers racing (worker + dashboard), so the balance
    is only ever credited once."""
    with connect(db_path) as conn:
        pos = conn.execute(
            "SELECT * FROM positions WHERE id = ? AND status = 'open'", (position_id,)
        ).fetchone()
        if pos is None:
            return None
        proceeds = exit_price * pos["contracts"] * 100
        pnl = proceeds - pos["cost_basis"]
        cur = conn.execute(
            """UPDATE positions
               SET status = 'closed', exit_price = ?, exit_time = ?,
                   exit_reason = ?, pnl = ?, suggested_exit_reason = NULL
               WHERE id = ? AND status = 'open'""",
            (exit_price, _now(), exit_reason, pnl, position_id),
        )
        return pnl if cur.rowcount == 1 else None
