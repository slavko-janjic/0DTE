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


# --- overnight range: two real bugs, both pinned -----------------------------

def _prepost_frame():
    """A realistic extended-hours frame: a regular session, then overnight bars
    that carry ZERO volume and bad ticks in High/Low (exactly what yfinance
    returns - a real SPY bar had close=749.86, low=749.82, high=754.68 on no
    volume, and another printed low=701.68 against a ~750 median)."""
    import pandas as pd
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    idx, rows = [], []
    # regular session 09:30-15:55, price ~700, with a genuine 690-710 range
    for m in range(0, 390, 5):
        idx.append(pd.Timestamp(2026, 7, 16, 9, 30, tz=et) + pd.Timedelta(minutes=m))
        rows.append({"Open": 700, "High": 710, "Low": 690, "Close": 700, "Volume": 500_000})
    # overnight 16:00-18:00, price pinned ~750, but with garbage High/Low ticks
    for m in range(0, 120, 5):
        idx.append(pd.Timestamp(2026, 7, 16, 16, 0, tz=et) + pd.Timedelta(minutes=m))
        rows.append({"Open": 750, "High": 754.68, "Low": 701.68, "Close": 750, "Volume": 0})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def test_overnight_range_excludes_the_regular_session(monkeypatch):
    """Bug 1: period='1d', prepost=True returns the WHOLE day, so max/min over it
    reported the full session's range mislabeled as 'overnight' - and that wrong
    level was drawn on the price chart."""
    import data.market_data as md
    frame = _prepost_frame()
    monkeypatch.setattr(md.yf, "Ticker", lambda s: type("T", (), {
        "history": staticmethod(lambda **k: frame)})())
    high, low = md.get_overnight_range("SPY")
    # the regular session traded 690-710; none of that may leak in
    assert low > 710


def test_overnight_range_ignores_bad_ticks_in_high_low(monkeypatch):
    """Bug 2: overnight bars have zero volume and garbage High/Low. Using them
    gave SPY a 7.55% overnight range against a ~750 spot. Closes are clean."""
    import data.market_data as md
    frame = _prepost_frame()
    monkeypatch.setattr(md.yf, "Ticker", lambda s: type("T", (), {
        "history": staticmethod(lambda **k: frame)})())
    high, low = md.get_overnight_range("SPY")
    assert (high, low) == (750.0, 750.0)      # from closes, not the 754.68/701.68 ticks


def test_overnight_range_none_during_the_regular_session(monkeypatch):
    """No overnight session in progress -> say so, don't hand back today's range."""
    import pandas as pd
    import data.market_data as md
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    idx = [pd.Timestamp(2026, 7, 16, 9, 30, tz=et) + pd.Timedelta(minutes=m)
           for m in range(0, 60, 5)]
    frame = pd.DataFrame(
        [{"Open": 700, "High": 701, "Low": 699, "Close": 700, "Volume": 1}] * len(idx),
        index=pd.DatetimeIndex(idx))
    monkeypatch.setattr(md.yf, "Ticker", lambda s: type("T", (), {
        "history": staticmethod(lambda **k: frame)})())
    assert md.get_overnight_range("SPY") is None


def test_held_contract_is_never_priced_off_another_expiration():
    # the chain is always the NEAREST expiry: yesterday's position must not be
    # valued (or closed) at today's contract that happens to share its strike
    import pandas as pd
    from types import SimpleNamespace
    from data.market_data import find_contract_price
    frame = pd.DataFrame([{"strike": 500.0, "bid": 1.90, "ask": 2.10, "lastPrice": 2.0}])
    chain = SimpleNamespace(expiration="2026-09-25", calls=frame, puts=frame)
    assert find_contract_price(chain, "call", 500.0, "2026-09-25") == 1.90
    assert find_contract_price(chain, "call", 500.0, "2026-09-24") is None
