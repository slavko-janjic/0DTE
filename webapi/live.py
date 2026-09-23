"""The parts of the API that must touch live market data, plus every write.

Kept apart from `payloads` so the read path stays pure SQLite and testable, and
so there is exactly one place to look for anything with a side effect.

Non-negotiables preserved from the Streamlit dashboard:
  * entries fill ask-side (`market_data.contract_entry_price`) - honest fills;
  * open positions are valued bid-side, so the round trip pays the real spread;
  * the same `price_target_exit` rule auto-closes on the UI's fast cadence, and
    `engine.close` is race-safe, so a simultaneous worker close cannot
    double-credit the balance.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from data import market_data
from paper_trading import engine
from paper_trading.models import Position
from storage import db as storage
from webapi import payloads
from worker import is_market_open, minutes_to_market_close, next_trading_day

log = logging.getLogger(__name__)

AUTOPILOT_MODES = ("off", "day", "continuous")


def _chain(ticker: str):
    """A chain fetch that degrades to None instead of raising: a flaky quote
    source must read as 'unavailable' in the UI, never as a 500."""
    try:
        return market_data.get_option_chain(ticker)
    except Exception:  # noqa: BLE001 - any upstream failure is just "no quote"
        log.warning("option chain fetch failed for %s", ticker, exc_info=True)
        return None


def refresh_open_positions(db_path: str, config: dict) -> tuple[dict, list[dict]]:
    """Re-price every open position from a fresh chain and run the per-trade
    profit-target / stop-loss check.

    Returns ({position_id: {price, spread_pct}}, [auto-close notices]). Positions
    whose quote is unavailable simply keep the worker's last stored price.
    """
    open_positions = storage.get_open_positions(db_path)
    if not open_positions:
        return {}, []

    chains = {}
    live: dict[int, dict] = {}
    closed: list[dict] = []
    for pos in open_positions:
        ticker = pos["ticker"]
        if ticker not in chains:
            chains[ticker] = _chain(ticker)
        chain = chains[ticker]
        price = market_data.find_contract_price(chain, pos["option_type"], pos["strike"])
        row = market_data.find_contract_row(chain, pos["option_type"], pos["strike"])
        spread = market_data.contract_spread_pct(row) if row is not None else None
        if price is None:
            continue

        storage.update_position_price(db_path, pos["id"], price)
        live[pos["id"]] = {"price": price, "spread_pct": spread}

        reason = engine.price_target_exit(Position.from_row(pos), price)
        if reason is not None and engine.close(db_path, pos["id"], price, reason) is not None:
            live.pop(pos["id"], None)
            closed.append({"id": pos["id"], "ticker": ticker,
                           "option_type": pos["option_type"],
                           "reason": reason.replace("_", " ")})
    return live, closed


def quote(ticker: str, option_type: str, amount: float | None = None) -> dict:
    """The ATM contract the Buy button would actually hit, at its ask-side fill
    price - so the form can show what the trade costs before it is placed."""
    chain = _chain(ticker)
    if chain is None:
        return {"available": False, "message": "Couldn't fetch a live quote right now."}
    frame = chain.calls if option_type == "call" else chain.puts
    contract = market_data.find_atm_contract(frame, chain.spot)
    if contract is None:
        return {"available": False, "message": "No option contract available for this ticker."}
    entry_price = market_data.contract_entry_price(contract)
    if entry_price is None:
        return {"available": False, "message": "No usable quote on that contract right now."}
    spread = market_data.contract_spread_pct(contract)
    contracts = engine.calculate_contracts(amount, 100.0, entry_price) if amount else None
    return {
        "available": True,
        "ticker": ticker,
        "option_type": option_type,
        "strike": float(contract["strike"]),
        "expiration": chain.expiration,
        "spot": chain.spot,
        "entry_price": entry_price,
        "spread_pct": spread,
        "contracts": contracts,
        "cost": round(entry_price * contracts * 100, 2) if contracts else None,
    }


def place_trade(db_path: str, config: dict, ticker: str, option_type: str,
                amount: float, profit_target_pct: float | None = None,
                stop_loss_pct: float | None = None) -> dict:
    """Buy the ATM 0DTE contract for `amount` dollars of risk, at the ask.

    Mirrors dashboard.py's place_trade exactly, with the per-trade exit targets
    the mockup's form exposes applied at entry.
    """
    if option_type not in ("call", "put"):
        return {"ok": False, "message": "Option type must be 'call' or 'put'."}
    if ticker not in config["tickers"]:
        return {"ok": False, "message": f"{ticker} isn't a tracked ticker."}
    if not amount or amount <= 0:
        return {"ok": False, "message": "Enter an amount to risk."}

    chain = _chain(ticker)
    if chain is None:
        return {"ok": False, "message": "Couldn't fetch a live quote right now - "
                                        "try again in a moment."}
    frame = chain.calls if option_type == "call" else chain.puts
    contract = market_data.find_atm_contract(frame, chain.spot)
    if contract is None:
        return {"ok": False, "message": "No option contract available for this ticker right now."}
    entry_price = market_data.contract_entry_price(contract)  # honest fill: ask-side
    if entry_price is None:
        return {"ok": False, "message": "No usable quote on that contract right now."}

    contracts = engine.calculate_contracts(amount, 100.0, entry_price)
    if contracts <= 0:
        return {"ok": False, "message": "That amount isn't enough for even one contract "
                                        "at the current price."}

    snap = storage.get_latest_signal(db_path, ticker)
    composite_score = snap["composite_score"] if snap is not None else 0.0
    position_id = engine.buy(
        db_path, ticker, option_type, float(contract["strike"]), chain.expiration,
        entry_price, contracts, composite_score,
    )
    if position_id is None:
        return {"ok": False, "message": "Trade rejected - insufficient balance."}

    if profit_target_pct is not None or stop_loss_pct is not None:
        storage.set_position_exit_targets(db_path, position_id, profit_target_pct, stop_loss_pct)

    return {
        "ok": True,
        "position_id": position_id,
        "message": f"Bought {contracts} {ticker} {contract['strike']:g} {option_type} "
                   f"@ ${entry_price:.2f}",
    }


def close_position(db_path: str, position_id: int) -> dict:
    """Close at the live bid-side price when one is available, else at the last
    price the worker saw (and, failing that, flat at entry)."""
    position = next((row for row in storage.get_open_positions(db_path)
                     if row["id"] == position_id), None)
    if position is None:
        return {"ok": False, "message": "That position is no longer open."}

    chain = _chain(position["ticker"])
    live_price = market_data.find_contract_price(chain, position["option_type"],
                                                 position["strike"])
    exit_price = live_price if live_price is not None else position["current_price"]
    if exit_price is None:
        exit_price = position["entry_price"]
    reason = position["suggested_exit_reason"] or "manual"

    pnl = engine.close(db_path, position_id, exit_price, reason)
    if pnl is None:
        return {"ok": False, "message": "That position was already closed."}
    return {"ok": True, "pnl": pnl,
            "message": f"Closed {position['ticker']} {position['option_type']} "
                       f"@ ${exit_price:.2f} (P&L ${pnl:+,.0f})"}


def set_targets(db_path: str, position_id: int, profit_target_pct: float | None,
                stop_loss_pct: float | None) -> dict:
    """Profit target is stored positive, stop loss negative - the UI sends both
    as magnitudes, so the sign is applied here in one place."""
    if not any(row["id"] == position_id for row in storage.get_open_positions(db_path)):
        return {"ok": False, "message": "That position is no longer open."}
    target = abs(profit_target_pct) if profit_target_pct is not None else None
    stop = -abs(stop_loss_pct) if stop_loss_pct is not None else None
    storage.set_position_exit_targets(db_path, position_id, target, stop)
    return {"ok": True, "profit_target_pct": target, "stop_loss_pct": stop}


def set_autopilot_mode(db_path: str, config: dict, mode: str) -> dict:
    """Off / Day session / Continuous. 'day' arms for today if the session can
    still trade, else for the next trading day - same rule the Streamlit control
    used, so the worker's auto-disarm keeps working unchanged."""
    if mode not in AUTOPILOT_MODES:
        return {"ok": False, "message": f"Mode must be one of {', '.join(AUTOPILOT_MODES)}."}

    if mode != "day":
        storage.set_autopilot_state(db_path, mode)
        return {"ok": True, "mode": mode, "armed_date": None}

    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    today = datetime.now(tz).date()
    tradeable_today = (
        is_market_open(config)
        or (today.weekday() < 5
            and today.isoformat() not in config.get("market_holidays", [])
            and minutes_to_market_close(config) > 0)
    )
    armed = today if tradeable_today else next_trading_day(config, today)
    storage.set_autopilot_state(db_path, "day", armed.isoformat())
    return {"ok": True, "mode": "day", "armed_date": armed.isoformat()}


def set_balance(db_path: str, balance: float) -> dict:
    if balance < 0:
        return {"ok": False, "message": "Balance can't be negative."}
    storage.set_balance(db_path, balance)
    return {"ok": True, "balance": balance}


def clear_history(db_path: str) -> dict:
    """Deletes closed trade records only - open positions and the balance are
    untouched."""
    storage.clear_closed_positions(db_path)
    return {"ok": True}


# --- tuning: self-calibration + per-ticker weights ------------------------

def set_calibration_enabled(db_path: str, enabled: bool) -> dict:
    """The nightly self-calibration pass runs in the worker; this is just its
    on/off switch, stored in the DB so it takes effect on the next cycle."""
    storage.set_calibration_enabled(db_path, bool(enabled))
    return {"ok": True, "enabled": storage.get_calibration_enabled(db_path)}


def revert_calibration(db_path: str, config: dict) -> dict:
    """Undo every auto-applied adjustment across all tickers: weight overrides,
    per-ticker inversions and confidence maps. Config defaults take over again
    on the worker's next cycle. Audited, like everything else calibration does."""
    for ticker in config["tickers"]:
        storage.clear_weight_overrides(db_path, ticker)
        for category in storage.get_inversions(db_path, ticker):
            storage.remove_inversion(db_path, ticker, category)
        storage.clear_confidence_bands(db_path, ticker)
        storage.log_calibration_event(db_path, ticker, "reverted", {})
    return {"ok": True}


def apply_weights(db_path: str, config: dict, ticker: str) -> dict:
    """Store the accuracy-based weight suggestion as this ticker's override.
    Per-ticker by design - each name keeps its own weights."""
    if ticker not in config["tickers"]:
        return {"ok": False, "message": f"{ticker} isn't a tracked ticker."}
    suggested = payloads.suggested_weights(db_path, config, ticker)
    if suggested is None:
        return {"ok": False, "message": "No category has enough graded history to "
                                        "justify a weight change yet."}
    storage.set_weight_overrides(db_path, ticker, suggested)
    return {"ok": True, "weights": suggested}


def revert_weights(db_path: str, config: dict, ticker: str) -> dict:
    if ticker not in config["tickers"]:
        return {"ok": False, "message": f"{ticker} isn't a tracked ticker."}
    storage.clear_weight_overrides(db_path, ticker)
    return {"ok": True}
