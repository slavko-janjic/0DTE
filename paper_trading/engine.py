"""Paper trading logic: position sizing, exit-condition evaluation, and P&L.

The pure functions here (calculate_contracts, evaluate_exit, calculate_pnl)
take plain values so they're unit-testable without a database or live data.
buy() / close_position() are thin wrappers that persist via storage.db and
are exercised by the worker/dashboard, not unit tested directly.
"""
import math

from paper_trading.models import Position
from storage import db as storage


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


def evaluate_exit(
    position: Position,
    current_price: float,
    current_composite_score: float | None,
    minutes_to_close: float,
    exit_rules: dict,
) -> str | None:
    """Returns an exit reason if any exit condition fires, else None.

    Checked in priority order: time cutoff (hard safety net) first, then
    profit target / stop loss, then signal reversal.
    """
    if minutes_to_close <= exit_rules.get("time_cutoff_minutes_before_close", 30):
        return "time_cutoff"

    _, pnl_pct = calculate_pnl(position.entry_price, current_price, position.contracts)
    if pnl_pct >= exit_rules.get("profit_target_pct", 50):
        return "profit_target"
    if pnl_pct <= exit_rules.get("stop_loss_pct", -30):
        return "stop_loss"

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
) -> int | None:
    balance = storage.get_balance(db_path)
    cost = entry_price * contracts * 100
    if contracts <= 0 or cost > balance:
        return None
    position_id = storage.open_position(
        db_path, ticker, option_type, strike, expiration, contracts,
        entry_price, entry_composite_score,
    )
    storage.set_balance(db_path, balance - cost)
    return position_id


def close(db_path: str, position_id: int, exit_price: float, exit_reason: str) -> float:
    """Closes a position and credits proceeds (cost_basis + pnl) back to the
    balance, since cost_basis was already deducted at buy time."""
    with storage.connect(db_path) as conn:
        cost_basis = conn.execute(
            "SELECT cost_basis FROM positions WHERE id = ?", (position_id,)
        ).fetchone()["cost_basis"]

    pnl = storage.close_position(db_path, position_id, exit_price, exit_reason)
    balance = storage.get_balance(db_path)
    storage.set_balance(db_path, balance + cost_basis + pnl)
    return pnl
