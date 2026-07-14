"""Honest-fill pricing: buys at the ask, sells at the bid, mid then lastPrice
as fallbacks when quotes are missing/zero (common for stale 0DTE chains)."""
import math

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
