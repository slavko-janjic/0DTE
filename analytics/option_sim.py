"""Simulated same-day option trades on a share-price path.

The research lab (analytics/session_lab.py) needs to know what the autopilot's
trade would have paid on thousands of past sessions, but there is no options
history - only share prices. So the option is priced with Black-Scholes off the
minute-by-minute share price, and the trade is run through the app's OWN exit
logic (paper_trading.engine.evaluate_exit) so the label means exactly what the
autopilot's exits mean.

Time is measured in minutes since the 09:30 open: "minute m" is the moment
m minutes after the open, and the price known at that moment is the close of
the bar that ended then. A path is {minute: share price}.

`lag_minutes` reproduces how the app's paper fills actually worked: Yahoo's
option quotes trail the share price by 15 minutes (measured 2026-10-19 -
option-quote changes correlate 0.69 with the share-price change 15 minutes
earlier and ~0 with the same minute). With lag 15, the simulated option at
minute m is priced off the share price at m-15, as the app saw it. The research
label itself uses lag 0 - a real-time trade.

Pure functions, no I/O.
"""
import math
from dataclasses import dataclass

from data.market_data import _RISK_FREE_RATE, _norm_cdf
from paper_trading.engine import evaluate_exit
from paper_trading.models import Position

SESSION_MINUTES = 390                      # 09:30 -> 16:00
_MINUTES_PER_YEAR = 365.0 * 24 * 60        # same calendar-year convention as market_data


def years_to_close(minute: float, close_minute: int = SESSION_MINUTES) -> float:
    """Time left to a 16:00 expiry, in years. Floored at one minute, as
    market_data._years_to_expiration floors at 60 seconds."""
    return max(close_minute - minute, 1.0) / _MINUTES_PER_YEAR


def bs_price(spot: float, strike: float, sigma: float, years: float, option_type: str,
             rate: float = _RISK_FREE_RATE) -> float:
    """European Black-Scholes price. Intrinsic value when there is no time or
    no volatility left."""
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if spot <= 0 or strike <= 0 or sigma <= 0 or years <= 0:
        return intrinsic
    root = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / root
    d2 = d1 - root
    discount = strike * math.exp(-rate * years)
    if option_type == "call":
        return spot * _norm_cdf(d1) - discount * _norm_cdf(d2)
    return discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price: float, spot: float, strike: float, years: float, option_type: str,
                rate: float = _RISK_FREE_RATE, low: float = 0.01, high: float = 5.0) -> float | None:
    """The volatility that reproduces `price` (bisection). None when the price
    is at or below intrinsic, or beyond what `high` can explain - a quote that
    can't be inverted says nothing about volatility."""
    if price is None or price <= 0 or years <= 0:
        return None
    if price <= bs_price(spot, strike, low, years, option_type, rate):
        return None
    if price >= bs_price(spot, strike, high, years, option_type, rate):
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        if bs_price(spot, strike, mid, years, option_type, rate) < price:
            low = mid
        else:
            high = mid
        if high - low < 1e-7:
            break
    return (low + high) / 2.0


def spot_at(path: dict, minute: int, max_gap: int = 5) -> float | None:
    """The latest share price known at `minute`: the path's value there, or
    the most recent one up to `max_gap` minutes earlier (a missing bar is not a
    missing price). None beyond that."""
    for back in range(max_gap + 1):
        price = path.get(minute - back)
        if price is not None:
            return price
    return None


@dataclass
class TradeResult:
    option_type: str
    strike: float
    entry_price: float
    exit_price: float
    ret_pct: float          # on the premium paid, after the spread
    exit_reason: str
    exit_minute: int


def simulate_trade(
    path: dict, entry_minute: int, strike: float, option_type: str, sigma: float,
    exit_cfg: dict, half_spread: float = 0.0, lag_minutes: int = 0,
    close_minute: int = SESSION_MINUTES,
) -> TradeResult | None:
    """Buys one same-day option at `entry_minute` and runs it to an exit.

    Entry pays the model price + half the spread; each later minute the
    position is marked at model price - half the spread (the app values open
    positions at the bid) and handed to evaluate_exit with the strategy's exit
    config and no composite score (reversal exits need a live signal). The
    first exit reason closes it. None when the entry can't be priced."""
    def model(minute: int) -> float | None:
        seen = minute - lag_minutes
        spot = spot_at(path, seen)
        if spot is None:
            return None
        return bs_price(spot, strike, sigma, years_to_close(seen, close_minute), option_type)

    entry_mid = model(entry_minute)
    if entry_mid is None or entry_mid <= 0:
        return None
    entry = entry_mid + half_spread
    position = Position(
        id=0, ticker="", option_type=option_type, strike=strike, expiration="",
        contracts=1, entry_price=entry, cost_basis=entry * 100, entry_composite_score=0.0,
        profit_target_pct=exit_cfg.get("profit_target_pct"),
        stop_loss_pct=exit_cfg.get("stop_loss_pct"), max_price=entry,
    )

    def result(mark: float, reason: str, minute: int) -> TradeResult:
        return TradeResult(option_type, strike, entry, mark,
                           (mark - entry) / entry * 100.0, reason, minute)

    mark = None
    for minute in range(entry_minute + 1, close_minute + 1):
        mid = model(minute)
        if mid is None:
            continue
        mark = max(mid - half_spread, 0.0)
        position.max_price = max(position.max_price, mark)
        reason = evaluate_exit(position, mark, None, close_minute - minute, exit_cfg)
        if reason:
            return result(mark, reason, minute)
    # no exit fired (an exit config without a time cutoff): it expires
    spot = spot_at(path, close_minute, max_gap=close_minute)
    if spot is None:
        return result(mark or 0.0, "expired", close_minute)
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    return result(intrinsic, "expired", close_minute)
