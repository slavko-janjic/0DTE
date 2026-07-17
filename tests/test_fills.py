"""Honest-fill pricing: buys at the ask, sells at the bid, mid then lastPrice
as fallbacks when quotes are missing/zero (common for stale 0DTE chains)."""
import math

import pytest

from data.market_data import (
    contract_entry_price, contract_exit_price, contract_spread_pct,
)


FULL = {"bid": 1.90, "ask": 2.10, "lastPrice": 2.05}


def test_entry_uses_ask_exit_uses_bid():
    assert contract_entry_price(FULL) == 2.10
    assert contract_exit_price(FULL) == 1.90


def test_missing_ask_falls_back_to_mid_then_last():
    no_ask = {"bid": 1.90, "ask": 0.0, "lastPrice": 2.05}
    # ask invalid and mid needs both -> lastPrice
    assert contract_entry_price(no_ask) == 2.05
    no_quotes = {"bid": 0.0, "ask": float("nan"), "lastPrice": 2.05}
    assert contract_entry_price(no_quotes) == 2.05
    assert contract_exit_price(no_quotes) == 2.05


def test_no_prices_at_all_returns_none():
    dead = {"bid": 0.0, "ask": 0.0, "lastPrice": 0.0}
    assert contract_entry_price(dead) is None
    assert contract_exit_price(dead) is None


def test_crossed_market_ignores_mid():
    # bid > ask (crossed/stale) -> mid untrusted; exit takes the bid directly
    crossed = {"bid": 2.50, "ask": 2.00, "lastPrice": 2.20}
    assert contract_exit_price(crossed) == 2.50
    assert contract_entry_price(crossed) == 2.00


def test_spread_pct():
    assert math.isclose(contract_spread_pct(FULL), (2.10 - 1.90) / 2.00 * 100.0)
    assert contract_spread_pct({"bid": 0.0, "ask": 2.0, "lastPrice": 1.0}) is None


def test_contract_quote_returns_full_cost_picture():
    from data.market_data import contract_quote
    bid, ask, mid, spread = contract_quote(FULL)
    assert (bid, ask, mid) == (1.90, 2.10, 2.00)
    assert spread == pytest.approx((2.10-1.90)/2.00*100)


def test_contract_quote_with_no_quotes():
    from data.market_data import contract_quote
    bid, ask, mid, spread = contract_quote({"bid": 0.0, "ask": 0.0, "lastPrice": 2.0})
    assert bid is None and ask is None and mid is None and spread is None


# --- picking the skew legs ---------------------------------------------------

def test_find_delta_contract_picks_the_closest_delta():
    import pandas as pd
    from data.market_data import find_delta_contract
    df = pd.DataFrame({
        "strike": [100.0, 105.0, 110.0, 115.0],
        "delta":  [0.70,  0.45,  0.26,  0.10],
    })
    assert find_delta_contract(df, 0.25)["strike"] == 110.0   # closest to 25-delta
    assert find_delta_contract(df, 0.70)["strike"] == 100.0


def test_find_delta_contract_handles_puts_negative_delta():
    import pandas as pd
    from data.market_data import find_delta_contract
    df = pd.DataFrame({"strike": [90.0, 95.0], "delta": [-0.24, -0.60]})
    assert find_delta_contract(df, -0.25)["strike"] == 90.0


def test_find_delta_contract_none_without_greeks():
    import pandas as pd
    from data.market_data import find_delta_contract
    assert find_delta_contract(pd.DataFrame({"strike": [100.0]}), 0.25) is None  # no delta col
    assert find_delta_contract(pd.DataFrame(), 0.25) is None
    # all-NaN deltas (BS solver failed on every strike)
    df = pd.DataFrame({"strike": [100.0], "delta": [float("nan")]})
    assert find_delta_contract(df, 0.25) is None
