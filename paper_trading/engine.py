"""Paper trading logic: position sizing, exit-condition evaluation, and P&L.

The pure functions here (calculate_contracts, evaluate_exit, calculate_pnl)
take plain values so they're unit-testable without a database or live data.
buy() / close_position() are thin wrappers that persist via storage.db and
are exercised by the worker/dashboard, not unit tested directly.
"""
import calendar as _cal
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from paper_trading.models import Position
from storage import db as storage

# Exit reasons the worker acts on automatically for ANY position (the per-trade
# stop/target the user explicitly set).
AUTO_CLOSE_REASONS = frozenset({"profit_target", "stop_loss"})

# Global risk exits: force-close AUTO positions (so a day-session is managed
# end to end) but only SUGGEST for manual positions, which the user manages.
# ("expired" is neither: it isn't a decision, the contract simply settled.)
AUTO_FORCE_REASONS = frozenset({"time_cutoff", "catalyst", "trailing_stop", "time_decay_stop"})


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


def trailing_stop_exit(position: Position, current_price: float, exit_rules: dict) -> str | None:
    """Lets winners run, then locks them in: once the premium has been up by
    trailing_activate_pct from entry (tracked via the high-water mark
    position.max_price), exit if it gives back trailing_stop_pct from that peak.
    Off when either knob is missing or the position never armed."""
    activate = exit_rules.get("trailing_activate_pct")
    give_back = exit_rules.get("trailing_stop_pct")
    if activate is None or give_back is None:
        return None
    peak = position.max_price if position.max_price is not None else position.entry_price
    peak = max(peak, current_price)  # be robust if the caller hasn't bumped it yet
    if position.entry_price <= 0:
        return None
    peak_gain_pct = (peak - position.entry_price) / position.entry_price * 100.0
    if peak_gain_pct < activate:
        return None  # never got far enough into profit to arm the trail
    give_back_pct = (peak - current_price) / peak * 100.0 if peak > 0 else 0.0
    return "trailing_stop" if give_back_pct >= give_back else None


def late_session_stop_exit(
    position: Position, current_price: float, minutes_to_close: float, exit_rules: dict,
) -> str | None:
    """Theta-aware: 0DTE decay accelerates late in the session, so a position
    still underwater in the last late_session_minutes is usually dead premium.
    Cut it once its loss reaches late_session_stop_pct (negative). Off when
    either knob is missing."""
    late_minutes = exit_rules.get("late_session_minutes")
    late_stop = exit_rules.get("late_session_stop_pct")
    if late_minutes is None or late_stop is None:
        return None
    if minutes_to_close > late_minutes:
        return None
    _, pnl_pct = calculate_pnl(position.entry_price, current_price, position.contracts)
    return "time_decay_stop" if pnl_pct <= late_stop else None


@dataclass
class AutoIntent:
    """A dry-run of the autopilot entry decision for one ticker: what it WOULD do
    right now, and - if it is standing down - the single reason why. Pure, so the
    dashboard, the worker's intent log, and should_auto_enter all agree."""
    ticker: str
    direction: str                  # 'bullish' | 'bearish' | 'neutral'
    lean: str | None                # 'call'/'put' the direction implies (None if neutral)
    confidence_pct: float
    min_confidence_pct: float       # the effective gate, incl. any positive-gamma penalty
    would_enter: bool
    blocker: str | None             # None when would_enter; else why it's standing down


def explain_auto_decision(
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
    calendar_blocker: str | None = None,
) -> AutoIntent:
    """The autopilot's entry decision, WITH its reasoning - the same guard rails
    as should_auto_enter, in the same order, but returning why it would stand
    down instead of a bare None. Surfaces the bot's live intent before it acts.

    calendar_blocker (from day_setup.calendar_blocker) is checked first: with an
    expired catalyst/holiday calendar no signal is safe to act on."""
    lean = {"bullish": "call", "bearish": "put"}.get(direction)
    min_conf = autopilot_cfg.get("min_confidence_pct", 55)
    gamma_penalty = autopilot_cfg.get("positive_gamma_confidence_penalty", 0)
    if gamma_regime == "positive":
        min_conf += gamma_penalty

    def _i(would, blocker):
        return AutoIntent(ticker, direction, lean, confidence_pct, min_conf, would, blocker)

    if calendar_blocker:
        return _i(False, calendar_blocker)

    if lean is None:
        return _i(False, "no directional signal (neutral)")

    if confidence_pct < min_conf:
        extra = " (raised for a rangebound/positive-gamma regime)" if (
            gamma_regime == "positive" and gamma_penalty) else ""
        return _i(False, f"confidence {confidence_pct:.0f}% is below the {min_conf:.0f}% gate{extra}")

    if (minutes_to_catalyst is not None
            and 0 <= minutes_to_catalyst <= autopilot_cfg.get("no_entry_before_catalyst_minutes", 15)):
        return _i(False, f"standing down near a scheduled catalyst ({minutes_to_catalyst:.0f} min out)")

    tactic = autopilot_cfg.get("tactic", "continuous")
    if tactic == "opening_range":
        ds = autopilot_cfg.get("decision_start_minutes", 30)
        de = autopilot_cfg.get("decision_end_minutes", 90)
        if minutes_since_open < ds:
            return _i(False, f"decision window opens in {ds - minutes_since_open:.0f} min")
        if minutes_since_open > de:
            return _i(False, "decision window has closed for today")
        if minutes_to_close < autopilot_cfg.get("no_entry_last_minutes", 60):
            return _i(False, "too little runway before the close")
    else:  # continuous
        first = autopilot_cfg.get("no_entry_first_minutes", 30)
        if minutes_since_open < first:
            return _i(False, f"waiting out the first {first:.0f} min of the session")
        if minutes_to_close < autopilot_cfg.get("no_entry_last_minutes", 60):
            return _i(False, "too little runway before the close")

    if any(row["ticker"] == ticker for row in open_rows):
        return _i(False, "already holding a position in this ticker")

    if (sum(1 for row in open_rows if row["opened_by"] == "auto")
            >= autopilot_cfg.get("max_concurrent_positions", 3)):
        return _i(False, "max concurrent auto positions reached")

    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()

    def _is_today(iso_ts):
        return bool(iso_ts) and datetime.fromisoformat(iso_ts).astimezone(tz).date() == today

    auto_entries_today = sum(
        1 for row in list(open_rows) + list(closed_rows)
        if row["opened_by"] == "auto" and _is_today(row["entry_time"])
    )
    if auto_entries_today >= autopilot_cfg.get("max_trades_per_day", 4):
        return _i(False, "daily auto-trade cap reached")
    if (tactic == "opening_range"
            and auto_entries_today >= autopilot_cfg.get("max_entries_per_session", 1)):
        return _i(False, "already used this session's auto entries")

    cooldown = timedelta(minutes=autopilot_cfg.get("cooldown_minutes", 30))
    for row in closed_rows:
        if row["ticker"] == ticker and row["exit_time"]:
            if now - datetime.fromisoformat(row["exit_time"]) < cooldown:
                return _i(False, f"cooldown after a recent {ticker} close")

    loss_limit = autopilot_cfg.get("daily_loss_limit_pct", 10) / 100.0 * starting_balance
    todays_auto_pnl = sum(
        (row["pnl"] or 0.0) for row in closed_rows
        if row["opened_by"] == "auto" and _is_today(row["exit_time"])
    )
    if todays_auto_pnl <= -loss_limit:
        return _i(False, "daily loss limit hit - stopped for today")

    return _i(True, None)


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
    calendar_blocker: str | None = None,
) -> str | None:
    """Auto-pilot entry decision: returns 'call'/'put' to enter, or None. Thin
    wrapper over explain_auto_decision (the single source of the guard-rail
    logic), so the acted-on decision and the surfaced intent can never drift."""
    intent = explain_auto_decision(
        ticker=ticker, direction=direction, confidence_pct=confidence_pct,
        minutes_since_open=minutes_since_open, minutes_to_close=minutes_to_close,
        open_rows=open_rows, closed_rows=closed_rows, autopilot_cfg=autopilot_cfg,
        starting_balance=starting_balance, now=now, tz_name=tz_name,
        minutes_to_catalyst=minutes_to_catalyst, gamma_regime=gamma_regime,
        calendar_blocker=calendar_blocker,
    )
    return intent.lean if intent.would_enter else None


def evaluate_exit(
    position: Position,
    current_price: float,
    current_composite_score: float | None,
    minutes_to_close: float,
    exit_rules: dict,
) -> str | None:
    """Returns an exit reason if any exit condition fires, else None.

    Checked in priority order: this position's own profit target / stop loss
    first, then the time cutoff (hard safety net), then the trailing stop (lock
    in a faded winner), then the late-session theta stop (cut a lingering
    loser), then signal reversal. Profit/stop are per-trade; the rest are global
    rules from exit_rules.

    Profit/stop MUST win over the time cutoff: the worker always executes them,
    but only suggests a time cutoff for manual positions - so checking the
    cutoff first silently disarmed a manual stop for the last 30 minutes.
    """
    price_exit = price_target_exit(position, current_price)
    if price_exit is not None:
        return price_exit

    if minutes_to_close <= exit_rules.get("time_cutoff_minutes_before_close", 30):
        return "time_cutoff"

    trailing = trailing_stop_exit(position, current_price, exit_rules)
    if trailing is not None:
        return trailing

    late = late_session_stop_exit(position, current_price, minutes_to_close, exit_rules)
    if late is not None:
        return late

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
    """Opens a position and debits its cost. None if contracts <= 0 or the
    balance can't cover it. The debit and the insert are one transaction (see
    storage.open_position), so a concurrent close can't lose the update."""
    if contracts <= 0:
        return None
    return storage.open_position(
        db_path, ticker, option_type, strike, expiration, contracts,
        entry_price, entry_composite_score, opened_by=opened_by, debit_balance=True,
    )


def close(db_path: str, position_id: int, exit_price: float, exit_reason: str) -> float | None:
    """Closes a position and credits proceeds (cost_basis + pnl) back to the
    balance, since cost_basis was already deducted at buy time. Returns None (and
    touches nothing) if the position was already closed - so two racing closers
    (worker + dashboard) can't double-credit the balance. The close and the
    credit are one transaction, so they can't be separated or lost either."""
    return storage.close_position(db_path, position_id, exit_price, exit_reason,
                                  credit_balance=True)


def settlement_price(option_type: str, strike: float, spot: float | None) -> float:
    """What an expired option is worth: its intrinsic value at the final spot
    (an ITM contract is exercised, an OTM one is worthless). 0.0 when the final
    spot is unknown - the conservative assumption."""
    if spot is None:
        return 0.0
    intrinsic = spot - strike if option_type == "call" else strike - spot
    return round(max(0.0, intrinsic), 2)
