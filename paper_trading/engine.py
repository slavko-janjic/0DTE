"""Paper trading logic: position sizing, exit-condition evaluation, and P&L.

The pure functions here (calculate_contracts, evaluate_exit, calculate_pnl)
take plain values so they're unit-testable without a database or live data.
buy() / close_position() are thin wrappers that persist via storage.db and
are exercised by the worker/dashboard, not unit tested directly.
"""
import calendar as _cal
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from paper_trading.models import Position
from storage import db as storage

# Exit reasons the worker acts on automatically (the per-trade stop/target the
# user explicitly set). Time-cutoff and signal-reversal stay suggestion-only.
AUTO_CLOSE_REASONS = frozenset({"profit_target", "stop_loss"})


def calculate_contracts(balance: float, risk_per_trade_pct: float, entry_price: float) -> int:
    """How many contracts to buy, capped by the configured risk budget.

    Returns 0 if even a single contract exceeds the risk budget.
    """
    if entry_price <= 0 or balance <= 0:
        return 0
    risk_budget = balance * (risk_per_trade_pct / 100.0)
    cost_per_contract = entry_price * 100
    return math.floor(risk_budget / cost_per_contract)


def calculate_pnl(entry_price: float, exit_price: float, contracts: int) -> tuple[float, float]:
    """Returns (pnl_dollars, pnl_pct) on the option premium."""
    cost_basis = entry_price * contracts * 100
    proceeds = exit_price * contracts * 100
    pnl_dollars = proceeds - cost_basis
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price else 0.0
    return pnl_dollars, pnl_pct


def summarize_pnl(
    open_rows: list, closed_rows: list, tz_name: str = "America/New_York",
    now: datetime | None = None,
) -> dict:
    """Aggregates P&L across positions for the account card: realized today
    (by exit date in the market timezone), realized all-time, and unrealized
    across open positions (skipping any without a current price yet). Takes
    plain row dicts/Rows so it's unit-testable without a database."""
    tz = ZoneInfo(tz_name)
    today = (now if now is not None else datetime.now(tz)).astimezone(tz).date()

    realized_today = 0.0
    realized_total = 0.0
    for row in closed_rows:
        pnl = row["pnl"] if row["pnl"] is not None else 0.0
        realized_total += pnl
        exit_time = row["exit_time"]
        if exit_time and datetime.fromisoformat(exit_time).astimezone(tz).date() == today:
            realized_today += pnl

    unrealized_open = 0.0
    for row in open_rows:
        if row["current_price"] is None:
            continue
        pnl_dollars, _ = calculate_pnl(row["entry_price"], row["current_price"], row["contracts"])
        unrealized_open += pnl_dollars

    return {
        "realized_today": realized_today,
        "realized_total": realized_total,
        "unrealized_open": unrealized_open,
    }


def daily_realized_pnl(closed_rows: list, tz_name: str = "America/New_York") -> dict:
    """Realized P&L bucketed by exit date (in the market timezone) - feeds the
    dashboard's daily P&L calendar. Returns {datetime.date: pnl}. Takes plain
    row dicts/Rows so it's unit-testable without a database."""
    tz = ZoneInfo(tz_name)
    by_day: dict = {}
    for row in closed_rows:
        exit_time = row["exit_time"]
        if not exit_time:
            continue
        day = datetime.fromisoformat(exit_time).astimezone(tz).date()
        by_day[day] = by_day.get(day, 0.0) + (row["pnl"] if row["pnl"] is not None else 0.0)
    return by_day


def trade_events(open_rows: list, closed_rows: list, ticker: str) -> list[dict]:
    """Entry/exit markers for a ticker's trades, for overlaying on the price chart.
    Returns [{time: datetime, kind: 'Entry'|'Exit', label: str}]. Open positions
    contribute an entry only; closed positions contribute entry + exit. Takes plain
    row dicts so it's unit-testable without a database."""
    events = []

    def _entry(row):
        return {
            "time": datetime.fromisoformat(row["entry_time"]),
            "kind": "Entry",
            "label": f"Entry {row['option_type']} {row['strike']:g} @ ${row['entry_price']:.2f}",
        }

    for row in open_rows:
        if row["ticker"] == ticker and row["entry_time"]:
            events.append(_entry(row))
    for row in closed_rows:
        if row["ticker"] != ticker:
            continue
        if row["entry_time"]:
            events.append(_entry(row))
        if row["exit_time"]:
            pnl = row["pnl"]
            pnl_str = f" (P&L ${pnl:+,.0f})" if pnl is not None else ""
            events.append({
                "time": datetime.fromisoformat(row["exit_time"]),
                "kind": "Exit",
                "label": f"Exit {row['option_type']} {row['strike']:g} @ ${row['exit_price']:.2f}{pnl_str}",
            })
    return events


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Month arithmetic with year rollover, e.g. shift_month(2026, 12, 1) -> (2027, 1)."""
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def month_calendar_cells(pnl_by_day: dict, year: int, month: int) -> list[list[dict]]:
    """Full weeks (Sunday-first) covering the given month, for the dashboard's
    month-grid P&L calendar. Each cell is {date, day, in_month, pnl}, where pnl
    is the realized total for that day (None if no closed trades). Weeks include
    spillover days from adjacent months, flagged in_month=False."""
    weeks = _cal.Calendar(firstweekday=6).monthdatescalendar(year, month)
    return [
        [
            {
                "date": day,
                "day": day.day,
                "in_month": day.month == month,
                "pnl": pnl_by_day.get(day),
            }
            for day in week
        ]
        for week in weeks
    ]


def price_target_exit(position: Position, current_price: float) -> str | None:
    """The per-trade profit-target / stop-loss check only (each off when None).
    Split out so the dashboard can auto-close on its faster ~15s cadence using
    exactly the same rule the worker applies inside evaluate_exit."""
    _, pnl_pct = calculate_pnl(position.entry_price, current_price, position.contracts)
    if position.profit_target_pct is not None and pnl_pct >= position.profit_target_pct:
        return "profit_target"
    if position.stop_loss_pct is not None and pnl_pct <= position.stop_loss_pct:
        return "stop_loss"
    return None


def should_auto_enter(
    ticker: str,
    direction: str,
    confidence_pct: float,
    minutes_since_open: float,
    minutes_to_close: float,
    open_rows: list,
    closed_rows: list,
    autopilot_cfg: dict,
    starting_balance: float,
    now: datetime,
    tz_name: str = "America/New_York",
    minutes_to_catalyst: float | None = None,
    gamma_regime: str | None = None,
) -> str | None:
    """Auto-pilot entry decision: returns 'call'/'put' to enter, or None.

    Pure and row-driven (like summarize_pnl) so it's unit-testable. Guard rails,
    checked in order: non-neutral direction, confidence threshold (raised in a
    positive-gamma/rangebound regime, where a directional breakout entry is
    riskier), catalyst proximity (stand down within
    no_entry_before_catalyst_minutes of a scheduled high-impact event), the
    tactic's entry window, no open position in this ticker (manual OR auto -
    never stacks), auto-concurrent cap, auto trades/day cap, per-ticker cooldown
    after any close, and a daily circuit breaker on realized auto P&L (pct of
    starting_balance).

    Tactics: 'opening_range' allows entries only inside the decision window
    (decision_start..decision_end minutes after open) and at most
    max_entries_per_session auto entries per day - one deliberate decision.
    'continuous' keeps the original behavior (any time outside the first/last
    minutes, up to max_trades_per_day).
    """
    if direction == "bullish":
        option_type = "call"
    elif direction == "bearish":
        option_type = "put"
    else:
        return None

    # positive gamma = dealers fade moves = rangebound, so breakout-style entries
    # need a higher bar; negative/neutral leave the threshold unchanged
    min_confidence = autopilot_cfg.get("min_confidence_pct", 55)
    if gamma_regime == "positive":
        min_confidence += autopilot_cfg.get("positive_gamma_confidence_penalty", 0)
    if confidence_pct < min_confidence:
        return None

    # stand down just before a scheduled high-impact catalyst (CPI/FOMC/earnings)
    # - getting caught long 0DTE premium into a known market-mover is a blowup
    if (minutes_to_catalyst is not None
            and 0 <= minutes_to_catalyst <= autopilot_cfg.get("no_entry_before_catalyst_minutes", 15)):
        return None

    tactic = autopilot_cfg.get("tactic", "continuous")
    if tactic == "opening_range":
        if not (autopilot_cfg.get("decision_start_minutes", 30)
                <= minutes_since_open
                <= autopilot_cfg.get("decision_end_minutes", 90)):
            return None
        if minutes_to_close < autopilot_cfg.get("no_entry_last_minutes", 60):
            return None  # half days: window may overlap the no-runway zone
    else:  # continuous
        if minutes_since_open < autopilot_cfg.get("no_entry_first_minutes", 30):
            return None
        if minutes_to_close < autopilot_cfg.get("no_entry_last_minutes", 60):
            return None

    # never stack on an existing position in this ticker, manual or auto
    if any(row["ticker"] == ticker for row in open_rows):
        return None

    if (sum(1 for row in open_rows if row["opened_by"] == "auto")
            >= autopilot_cfg.get("max_concurrent_positions", 3)):
        return None

    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()

    def _is_today(iso_ts: str | None) -> bool:
        return bool(iso_ts) and datetime.fromisoformat(iso_ts).astimezone(tz).date() == today

    auto_entries_today = sum(
        1 for row in list(open_rows) + list(closed_rows)
        if row["opened_by"] == "auto" and _is_today(row["entry_time"])
    )
    if auto_entries_today >= autopilot_cfg.get("max_trades_per_day", 4):
        return None
    if (tactic == "opening_range"
            and auto_entries_today >= autopilot_cfg.get("max_entries_per_session", 1)):
        return None

    # cooldown: any trade in this ticker closed within the last N minutes
    cooldown = timedelta(minutes=autopilot_cfg.get("cooldown_minutes", 30))
    for row in closed_rows:
        if row["ticker"] == ticker and row["exit_time"]:
            if now - datetime.fromisoformat(row["exit_time"]) < cooldown:
                return None

    # circuit breaker: today's realized AUTO P&L has burned through the daily limit
    loss_limit = autopilot_cfg.get("daily_loss_limit_pct", 10) / 100.0 * starting_balance
    todays_auto_pnl = sum(
        (row["pnl"] or 0.0) for row in closed_rows
        if row["opened_by"] == "auto" and _is_today(row["exit_time"])
    )
    if todays_auto_pnl <= -loss_limit:
        return None

    return option_type


def evaluate_exit(
    position: Position,
    current_price: float,
    current_composite_score: float | None,
    minutes_to_close: float,
    exit_rules: dict,
) -> str | None:
    """Returns an exit reason if any exit condition fires, else None.

    Checked in priority order: time cutoff (hard safety net) first, then this
    position's own profit target / stop loss (each off when None), then signal
    reversal. Profit/stop are per-trade; time-cutoff and reversal are global
    safety nets from exit_rules.
    """
    if minutes_to_close <= exit_rules.get("time_cutoff_minutes_before_close", 30):
        return "time_cutoff"

    price_exit = price_target_exit(position, current_price)
    if price_exit is not None:
        return price_exit

    if current_composite_score is not None:
        reversal_threshold = exit_rules.get("reversal_confidence_pct", 60) / 100.0
        entered_bullish = position.option_type == "call"
        now_bearish_enough = current_composite_score <= -reversal_threshold
        now_bullish_enough = current_composite_score >= reversal_threshold
        if entered_bullish and now_bearish_enough:
            return "signal_reversal"
        if not entered_bullish and now_bullish_enough:
            return "signal_reversal"

    return None


# --- persistence wrappers (used by worker.py / dashboard.py) ---------------

def buy(
    db_path: str,
    ticker: str,
    option_type: str,
    strike: float,
    expiration: str,
    entry_price: float,
    contracts: int,
    entry_composite_score: float,
    opened_by: str = "manual",
) -> int | None:
    balance = storage.get_balance(db_path)
    cost = entry_price * contracts * 100
    if contracts <= 0 or cost > balance:
        return None
    position_id = storage.open_position(
        db_path, ticker, option_type, strike, expiration, contracts,
        entry_price, entry_composite_score, opened_by=opened_by,
    )
    storage.set_balance(db_path, balance - cost)
    return position_id


def close(db_path: str, position_id: int, exit_price: float, exit_reason: str) -> float | None:
    """Closes a position and credits proceeds (cost_basis + pnl) back to the
    balance, since cost_basis was already deducted at buy time. Returns None (and
    touches nothing) if the position was already closed - so two racing closers
    (worker + dashboard) can't double-credit the balance."""
    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT cost_basis FROM positions WHERE id = ? AND status = 'open'", (position_id,)
        ).fetchone()
    if row is None:
        return None
    cost_basis = row["cost_basis"]

    pnl = storage.close_position(db_path, position_id, exit_price, exit_reason)
    if pnl is None:  # lost the race - another closer got it first
        return None
    balance = storage.get_balance(db_path)
    storage.set_balance(db_path, balance + cost_basis + pnl)
    return pnl
