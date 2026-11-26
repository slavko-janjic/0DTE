"""The risk gate: plain rules every agent order passes before any fill.

Pure: it takes the agent's account state, the market state and the price the
order would fill at, and returns None (allowed) or a Rejection naming the rule.
No model is involved and an agent can't argue with it - its prompt only ever
sees the rejection.

Limits come from config `risk` (global). An agent's own `risk` block can only
TIGHTEN them: for every numeric limit the smaller value wins, and its allowed
assets are intersected with the global list.

Closing (sell) is always allowed while the market is open, whatever the loss
limits say - reducing risk should never be blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from broker.models import OrderRequest

DEFAULT_LIMITS = {
    "max_position_pct": 20.0,        # value of one position after the buy, % of equity
    "max_0dte_premium_pct": 10.0,    # premium in an option expiring today, % of equity
    "max_daily_loss_pct": 6.0,       # from the day's starting equity; blocks new buys
    "max_drawdown_pct": 30.0,        # from peak equity; pauses the agent
    "max_open_positions": 8,
    "zero_dte_cutoff_minutes": 30,   # no new 0DTE buys this close to the bell
    "allowed_assets": ["stock", "option"],
}
NUMERIC = ("max_position_pct", "max_0dte_premium_pct", "max_daily_loss_pct",
           "max_drawdown_pct", "max_open_positions", "zero_dte_cutoff_minutes")
# for these a LARGER agent value is the tighter one
LARGER_IS_TIGHTER = {"zero_dte_cutoff_minutes"}


@dataclass
class Rejection:
    rule: str
    detail: str


@dataclass
class AccountState:
    equity: float
    cash: float
    peak_equity: float
    start_equity: float              # equity at the start of today's session
    paused: bool = False
    held: dict[str, dict] = field(default_factory=dict)   # instrument -> {qty, price}


@dataclass
class MarketState:
    open: bool
    today: str                       # exchange-time date YYYY-MM-DD
    minutes_to_close: float
    catalyst_imminent: bool = False


def effective_limits(config: dict, agent_cfg: dict | None = None) -> dict:
    limits = {**DEFAULT_LIMITS, **(config.get("risk") or {})}
    own = (agent_cfg or {}).get("risk") or {}
    for key in NUMERIC:
        if key in own and own[key] is not None:
            pick = max if key in LARGER_IS_TIGHTER else min
            limits[key] = pick(limits[key], own[key])
    if own.get("allowed_assets"):
        limits["allowed_assets"] = [a for a in limits["allowed_assets"] if a in own["allowed_assets"]]
    return limits


def check(order: OrderRequest, fill_price: float, est_fees: float, acct: AccountState,
          market: MarketState, limits: dict) -> Rejection | None:
    """fill_price: per share / per option share the order would fill at now
    (the ask for a buy, the bid for a sell)."""
    if not market.open:
        return Rejection("market_closed", "orders are accepted during the regular session only")
    if order.asset == "option" and order.limit_price is None:
        return Rejection("limit_required", "option orders must carry a limit price")
    is_0dte = order.asset == "option" and order.expiration == market.today
    if order.asset == "option" and order.expiration < market.today:
        return Rejection("expired", f"{order.expiration} has already expired")

    if order.side == "sell":
        held = acct.held.get(order.instrument, {}).get("qty", 0)
        if held < order.qty:
            return Rejection("not_held", f"holds {held} of {order.instrument}; selling more would be a short")
        if order.limit_price is not None and fill_price < order.limit_price:
            return Rejection("limit_not_reached", f"bid {fill_price:.2f} is below the limit {order.limit_price:.2f}")
        return None

    # --- buys ---
    if acct.paused:
        return Rejection("paused", "this agent is paused; it may only close positions")
    if order.asset not in limits["allowed_assets"]:
        return Rejection("asset_not_allowed", f"{order.asset} is not allowed for this agent")
    if acct.peak_equity > 0 and acct.equity <= acct.peak_equity * (1 - limits["max_drawdown_pct"] / 100):
        return Rejection("max_drawdown", f"equity {acct.equity:.0f} is {limits['max_drawdown_pct']:g}%+ below "
                                         f"its peak {acct.peak_equity:.0f}")
    if acct.start_equity > 0 and acct.equity <= acct.start_equity * (1 - limits["max_daily_loss_pct"] / 100):
        return Rejection("daily_loss_limit", f"down {100 * (1 - acct.equity / acct.start_equity):.1f}% today; "
                                             "no new positions until tomorrow")
    if is_0dte and market.catalyst_imminent:
        return Rejection("catalyst", "a high-impact release is imminent; no new 0DTE positions")
    if is_0dte and market.minutes_to_close < limits["zero_dte_cutoff_minutes"]:
        return Rejection("late_0dte", f"{market.minutes_to_close:.0f} min to the close; 0DTE entries stop "
                                      f"{limits['zero_dte_cutoff_minutes']} min before")
    if order.limit_price is not None and fill_price > order.limit_price:
        return Rejection("limit_not_reached", f"ask {fill_price:.2f} is above the limit {order.limit_price:.2f}")
    if order.instrument not in acct.held and len(acct.held) >= limits["max_open_positions"]:
        return Rejection("max_open_positions", f"already {len(acct.held)} open positions")
    cost = fill_price * order.qty * order.multiplier
    if cost + est_fees > acct.cash:
        return Rejection("insufficient_cash", f"needs {cost + est_fees:.2f}, has {acct.cash:.2f}")
    existing = acct.held.get(order.instrument, {})
    after = cost + existing.get("qty", 0) * existing.get("price", fill_price) * order.multiplier
    if after > acct.equity * limits["max_position_pct"] / 100:
        return Rejection("max_position_size", f"{order.instrument} would be {after:.0f}, over "
                                              f"{limits['max_position_pct']:g}% of equity {acct.equity:.0f}")
    if is_0dte and after > acct.equity * limits["max_0dte_premium_pct"] / 100:
        return Rejection("max_0dte_premium", f"0DTE premium {after:.0f} is over "
                                             f"{limits['max_0dte_premium_pct']:g}% of equity")
    return None
