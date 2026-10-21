"""Is the morning edge in the direction, or in the option model?

The research lab's morning signals beat a coin flip by ~+2-3% of premium on a
SIMULATED 0DTE option (analytics/session_lab, intraday_lab). The simulator's
absolute level is untrustworthy, and its relative edge could in principle come
from how Black-Scholes treats calls vs puts after a move. This module asks the
same question with no option at all: hold the SHARE in the signal's direction,
exiting where the option would have exited.

Pre-registered 2026-10-21 (before running):
- Signals: the four forward-tested morning signals, follow side only -
  gap_behaviour and orb15 at 10:00, trail30 and orb15 at 10:30.
- Trade: at the decision minute, find the share prices at which the ATM option
  (Black-Scholes, the label's vol) would be worth +50% and -35% of its price
  (static levels, set once at entry). Hold the share long (bullish) or short
  (bearish) until one is touched, else exit at 15:30. Return in basis points.
- Label per session: {"call": long return, "put": short return} so
  session_lab's scoring applies unchanged: excess = side - coin average, one
  observation per day, four periods.
- Costs: 1 bp round trip (a 1-cent spread on a ~$700 share plus commission is
  well under that), taken off both sides.
- Verdict: 4 tests, noise floor sqrt(2 ln 4) = 1.67; PASS = t >= 1.67, excess
  positive in all four periods, net positive since 2022.
- Descriptive only: the same with a plain hold to 15:30.

Pure functions; research.py does the I/O.
"""
from analytics import option_sim, session_lab

CUTOFF_MINUTE = 360          # 15:30, the autopilot's time cutoff
ROUND_TRIP_BPS = 1.0


def option_barriers(spot: float, sigma: float, minute: int, option_type: str,
                    target_pct: float = 50.0, stop_pct: float = -35.0) -> tuple[float, float] | None:
    """(favourable, adverse) share prices at which the ATM option bought at
    `minute` would be worth +target / -stop of its price - the share-move
    equivalent of the option's exits, fixed at entry."""
    strike = float(round(spot))
    years = option_sim.years_to_close(minute)
    price = option_sim.bs_price(spot, strike, sigma, years, option_type)
    if price <= 0:
        return None

    def solve(goal: float, lo: float, hi: float) -> float:
        for _ in range(80):
            mid = (lo + hi) / 2
            value = option_sim.bs_price(mid, strike, sigma, years, option_type)
            rising = option_type == "call"
            if (value < goal) == rising:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    up_goal = price * (1 + target_pct / 100.0)
    down_goal = price * (1 + stop_pct / 100.0)
    if option_type == "call":
        return solve(up_goal, spot, spot * 1.2), solve(down_goal, spot * 0.8, spot)
    return solve(up_goal, spot * 0.8, spot), solve(down_goal, spot, spot * 1.2)


def share_trade(path: dict, entry_minute: int, direction: int, favourable: float | None,
                adverse: float | None, cutoff: int = CUTOFF_MINUTE) -> float | None:
    """Signed return in bps of holding the share from entry_minute: +1 long,
    -1 short. Exits at the first touch of either level (None = no level), else
    at `cutoff`."""
    entry = option_sim.spot_at(path, entry_minute)
    if entry is None:
        return None
    last = entry
    for minute in range(entry_minute + 1, cutoff + 1):
        price = option_sim.spot_at(path, minute)
        if price is None:
            continue
        last = price
        if favourable is not None and (price - favourable) * direction >= 0:
            break
        if adverse is not None and (price - adverse) * direction <= 0:
            break
    return direction * (last - entry) / entry * 10000.0


def label_session(session: session_lab.Session, sigma: float, decision_minute: int,
                  barriers: bool = True) -> dict | None:
    """{'call': long bps, 'put': short bps} net of ROUND_TRIP_BPS - named like
    the option label so session_lab.evaluate_test scores it unchanged."""
    path = session.path()
    spot = option_sim.spot_at(path, decision_minute)
    if spot is None or not sigma:
        return None
    out = {}
    for side, direction, option_type in (("call", 1, "call"), ("put", -1, "put")):
        levels = option_barriers(spot, sigma, decision_minute, option_type) if barriers else (None, None)
        if levels is None:
            return None
        bps = share_trade(path, decision_minute, direction, *levels)
        if bps is None:
            return None
        out[side] = bps - ROUND_TRIP_BPS
    return out


TESTS = (("gap_behaviour", 30, "10:00"), ("orb15", 30, "10:00"),
         ("trail30", 60, "10:30"), ("orb15", 60, "10:30"))
