"""The one door every agent order goes through: validate -> quote -> risk
gate -> fill -> book. Also marks open positions and settles expired options.

    result = submit(db_path, config, OrderRequest(...), agent_cfg)
    result["status"]  # 'filled' | 'rejected'
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from broker import risk
from broker.models import OrderRequest, validate_shape
from broker.venues import SimVenue, VenueError, venue_for
from storage import agent_ledger as ledger

log = logging.getLogger("broker")
DEFAULT_STARTING_BALANCE = 5000.0


def _tz(config):
    return ZoneInfo(config.get("market_hours", {}).get("timezone", "America/New_York"))


def market_state(config: dict, now: datetime | None = None) -> risk.MarketState:
    import worker   # the session calendar lives there (holidays, half days, catalysts)
    now = (now or datetime.now(_tz(config))).astimezone(_tz(config))
    is_open = worker.is_market_open(config, now)
    return risk.MarketState(
        open=is_open, today=now.date().isoformat(),
        minutes_to_close=worker.minutes_to_market_close(config, now) if is_open else 0,
        catalyst_imminent=worker._catalyst_imminent(config, now))


def account_state(db_path: str, config: dict, agent: str, venue: SimVenue | None = None,
                  today: str | None = None, remark: bool = True) -> risk.AccountState:
    """Equity at current marks (re-marked now unless remark=False), with the
    peak and today's starting equity updated."""
    venue = venue or SimVenue(config)
    acct = ledger.get_account(db_path, agent)
    held, value = {}, 0.0
    for p in ledger.open_positions(db_path, agent):
        price = None
        if remark:
            try:
                price = venue.mark(p)
            except Exception as exc:  # noqa: BLE001 - a failed quote keeps the last mark
                log.warning("mark %s failed: %s", p["instrument"], exc)
            if price:
                ledger.mark(db_path, p["id"], price)
        price = price or p["current_price"] or p["avg_cost"]
        held[p["instrument"]] = {"qty": p["qty"], "price": price}
        value += price * p["qty"] * p["multiplier"]
    equity = acct["cash"] + value
    today = today or datetime.now(_tz(config)).date().isoformat()
    return risk.AccountState(
        equity=equity, cash=acct["cash"], peak_equity=ledger.update_peak(db_path, agent, equity),
        start_equity=ledger.day_start_equity(db_path, agent, today, equity),
        paused=bool(acct["paused"]), held=held)


def _reject(db_path, order, rule, detail):
    ledger.record_rejection(db_path, order.agent, rule, detail, order.to_dict())
    log.info("rejected %s %s %s: %s (%s)", order.agent, order.side, order.instrument, rule, detail)
    return {"status": "rejected", "rule": rule, "detail": detail, "instrument": order.instrument}


def submit(db_path: str, config: dict, order: OrderRequest, agent_cfg: dict | None = None,
           venue=None, market: risk.MarketState | None = None) -> dict:
    problem = validate_shape(order)
    if problem:
        return _reject(db_path, order, "invalid", problem)
    ledger.ensure_account(db_path, order.agent,
                          (agent_cfg or {}).get("starting_balance", DEFAULT_STARTING_BALANCE))
    venue = venue or venue_for(order, config)
    market = market or market_state(config)
    acct = account_state(db_path, config, order.agent, venue=venue, today=market.today)
    try:
        price = venue.quote(order)
    except Exception as exc:  # noqa: BLE001
        return _reject(db_path, order, "no_quote", str(exc)[:200])
    if not price:
        return _reject(db_path, order, "no_quote", f"no live price for {order.instrument}")
    limits = risk.effective_limits(config, agent_cfg)
    rejection = risk.check(order, price, venue.fees(order), acct, market, limits)
    if rejection:
        if rejection.rule == "max_drawdown":
            ledger.set_paused(db_path, order.agent, True, rejection.detail)
        return _reject(db_path, order, rejection.rule, rejection.detail)
    try:
        fill = venue.execute(order, price)
    except VenueError as exc:
        return _reject(db_path, order, "venue_error", str(exc)[:200])
    fees = venue.fees(order, fill.qty)
    booked = ledger.record_fill(
        db_path, agent=order.agent, instrument=order.instrument, asset=order.asset,
        symbol=order.symbol, option_type=order.option_type, strike=order.strike,
        expiration=order.expiration, multiplier=order.multiplier, side=order.side, qty=fill.qty,
        price=fill.price, fees=fees, venue=fill.venue, venue_order_id=fill.venue_order_id,
        reason=order.reason)
    log.info("filled %s %s %d %s @ %.4f on %s (fees %.2f)", order.agent, order.side, fill.qty,
             order.instrument, fill.price, fill.venue, fees)
    return {"status": "filled", "instrument": order.instrument, "side": order.side, "qty": fill.qty,
            "price": fill.price, "fees": fees, "venue": fill.venue, "cash": booked["cash"],
            "realized": booked["realized"] if order.side == "sell" else None}


def settle_expired(db_path: str, config: dict, spot_on=None, now: datetime | None = None) -> int:
    """Close options past their expiry at intrinsic value (the final spot).
    An option expiring today settles once today's session has closed."""
    from paper_trading.engine import settlement_price
    if spot_on is None:
        spot_on = _closing_spot
    now = (now or datetime.now(_tz(config))).astimezone(_tz(config))
    today = now.date().isoformat()
    after_close = now.hour >= 16 or (today in config.get("market_half_days", []) and now.hour >= 13)
    done = 0
    for p in ledger.open_positions(db_path):
        if p["asset"] != "option" or not p["expiration"]:
            continue
        if p["expiration"] > today or (p["expiration"] == today and not after_close):
            continue
        spot = spot_on(p["symbol"], p["expiration"])
        value = settlement_price(p["option_type"], p["strike"], spot)
        ledger.record_fill(db_path, agent=p["agent"], instrument=p["instrument"], asset="option",
                           symbol=p["symbol"], option_type=p["option_type"], strike=p["strike"],
                           expiration=p["expiration"], multiplier=p["multiplier"], side="expire",
                           qty=p["qty"], price=value, fees=0.0, venue="expiry",
                           reason=f"expired; final spot {spot}")
        done += 1
    return done


def _closing_spot(symbol: str, day: str) -> float | None:
    from data import market_data
    bars = market_data.get_daily_bars(symbol, period="1mo")
    if bars is None or bars.empty:
        return None
    for ts, row in bars.iterrows():
        if ts.date().isoformat() == day:
            return float(row["Close"])
    return None


def portfolio(db_path: str, config: dict, agent: str, remark: bool = True, venue=None) -> dict:
    """An agent's book for its prompt and the web page."""
    acct = ledger.get_account(db_path, agent)
    if acct is None:
        return {}
    state = account_state(db_path, config, agent, venue=venue, remark=remark)
    positions = []
    for p in ledger.open_positions(db_path, agent):
        price = state.held.get(p["instrument"], {}).get("price")
        positions.append({
            "instrument": p["instrument"], "asset": p["asset"], "qty": p["qty"],
            "avg_cost": round(p["avg_cost"], 4), "price": price,
            "value": round(price * p["qty"] * p["multiplier"], 2) if price else None,
            "unrealized": round((price - p["avg_cost"]) * p["qty"] * p["multiplier"], 2) if price else None,
            "opened_at": p["opened_at"], "expiration": p["expiration"]})
    return {"agent": agent, "cash": round(state.cash, 2), "equity": round(state.equity, 2),
            "starting_balance": acct["starting_balance"],
            "return_pct": round((state.equity / acct["starting_balance"] - 1) * 100, 2),
            "today_pct": round((state.equity / state.start_equity - 1) * 100, 2) if state.start_equity else None,
            "drawdown_pct": round((1 - state.equity / state.peak_equity) * 100, 2) if state.peak_equity else None,
            "paused": bool(acct["paused"]), "pause_reason": acct["pause_reason"], "positions": positions}
