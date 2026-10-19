"""option_sim: Black-Scholes pricing, IV inversion, and the trade simulator
running the app's own exit rules on a share-price path."""
import pytest

from analytics import option_sim as sim

EXITS = {"profit_target_pct": 50, "stop_loss_pct": -35, "time_cutoff_minutes_before_close": 30,
         "reversal_confidence_pct": 101}


def test_put_call_parity_and_intrinsic_at_expiry():
    s, k, sigma, t = 500.0, 501.0, 0.15, sim.years_to_close(30)
    call, put = sim.bs_price(s, k, sigma, t, "call"), sim.bs_price(s, k, sigma, t, "put")
    import math
    assert call - put == pytest.approx(s - k * math.exp(-sim._RISK_FREE_RATE * t), abs=1e-9)
    assert sim.bs_price(505.0, 500.0, 0.15, 0.0, "call") == 5.0
    assert sim.bs_price(505.0, 500.0, 0.15, 0.0, "put") == 0.0


def test_implied_vol_round_trips_and_rejects_impossible_prices():
    t = sim.years_to_close(31)
    for sigma in (0.08, 0.2, 0.6):
        price = sim.bs_price(600.0, 600.0, sigma, t, "call")
        assert sim.implied_vol(price, 600.0, 600.0, t, "call") == pytest.approx(sigma, rel=1e-4)
    assert sim.implied_vol(0.0, 600.0, 600.0, t, "call") is None
    assert sim.implied_vol(4.0, 610.0, 600.0, t, "call") is None      # below intrinsic


def test_spot_at_carries_the_last_price_forward_but_not_forever():
    path = {30: 100.0, 33: 101.0}
    assert sim.spot_at(path, 32) == 100.0
    assert sim.spot_at(path, 33) == 101.0
    assert sim.spot_at(path, 40) is None                     # more than 5 minutes stale


def _flat_then(move_at: int, to: float, start: float = 500.0) -> dict:
    return {m: (start if m < move_at else to) for m in range(0, 391)}


def test_a_strong_move_hits_the_target_and_the_other_side_the_stop():
    path = _flat_then(60, 503.0)                # +0.6% half an hour after entry
    call = sim.simulate_trade(path, 30, 500.0, "call", 0.15, EXITS)
    put = sim.simulate_trade(path, 30, 500.0, "put", 0.15, EXITS)
    assert call.exit_reason == "profit_target" and call.exit_minute == 60
    assert call.ret_pct >= 50
    assert put.exit_reason == "stop_loss" and put.ret_pct <= -35


def test_a_flat_day_decays_into_the_time_cutoff():
    trade = sim.simulate_trade(_flat_then(999, 500.0), 30, 500.0, "call", 0.15,
                               {"time_cutoff_minutes_before_close": 30})
    assert trade.exit_reason == "time_cutoff" and trade.exit_minute == 360
    assert trade.ret_pct < 0                    # pure time decay


def test_trailing_stop_uses_the_engine_rules():
    path = {m: 500.0 for m in range(0, 391)}
    for m in range(40, 391):
        path[m] = 502.0 if m < 70 else 500.6     # run up, then give most of it back
    exits = {"trailing_activate_pct": 40, "trailing_stop_pct": 20,
             "time_cutoff_minutes_before_close": 30}
    trade = sim.simulate_trade(path, 30, 500.0, "call", 0.15, exits)
    assert trade.exit_reason == "trailing_stop" and trade.exit_minute == 70


def test_spread_is_paid_on_both_sides():
    path = _flat_then(999, 500.0)
    cutoff_only = {"time_cutoff_minutes_before_close": 30}     # same exit for both
    free = sim.simulate_trade(path, 30, 500.0, "call", 0.15, cutoff_only)
    costly = sim.simulate_trade(path, 30, 500.0, "call", 0.15, cutoff_only, half_spread=0.05)
    assert costly.entry_price == pytest.approx(free.entry_price + 0.05)
    assert costly.ret_pct < free.ret_pct


def test_quote_lag_prices_the_option_off_the_earlier_share_price():
    # the share jumped 10 minutes before entry; a 15-minute-stale quote doesn't know yet
    path = _flat_then(20, 503.0)
    live = sim.simulate_trade(path, 30, 503.0, "call", 0.15, EXITS)
    stale = sim.simulate_trade(path, 30, 503.0, "call", 0.15, EXITS, lag_minutes=15)
    assert stale.entry_price < live.entry_price       # bought cheap on the old price...
    assert stale.ret_pct > live.ret_pct               # ...and the catch-up looks like profit


def test_unpriceable_entry_returns_none():
    assert sim.simulate_trade({}, 30, 500.0, "call", 0.15, EXITS) is None
