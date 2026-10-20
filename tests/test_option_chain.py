"""get_option_chain: a chain Yahoo returns with one side missing is a failed
fetch (None), not a snapshot callers will crash on."""
from types import SimpleNamespace

import pandas as pd
import pytest

from data import market_data


FRAME = pd.DataFrame({"strike": [240.0], "volume": [10], "lastPrice": [1.2]})


def _patch_yahoo(monkeypatch, calls, puts):
    fake_ticker = SimpleNamespace(
        option_chain=lambda exp: SimpleNamespace(calls=calls, puts=puts))
    monkeypatch.setattr(market_data.yf, "Ticker", lambda ticker: fake_ticker)
    monkeypatch.setattr(market_data, "get_current_price", lambda ticker: 240.0)


def test_complete_chain_is_returned(monkeypatch):
    _patch_yahoo(monkeypatch, FRAME, FRAME)
    chain = market_data.get_option_chain("IWM", "2099-01-02")
    assert chain is not None and chain.spot == 240.0
    assert chain.calls is FRAME and chain.puts is FRAME


@pytest.mark.parametrize("calls, puts", [(None, FRAME), (FRAME, None), (None, None)])
def test_chain_with_a_missing_side_is_a_failed_fetch(monkeypatch, calls, puts):
    _patch_yahoo(monkeypatch, calls, puts)
    assert market_data.get_option_chain("IWM", "2099-01-02") is None


# --- the 15-minute quote delay -------------------------------------------------------

from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402

_EXP = (_date.today() + _td(days=7)).isoformat()       # keeps time-to-expiry well above zero
_SIGMA = 0.15


def _quoted(spot_then: float, strikes, option_type: str, half_spread: float = 0.01) -> pd.DataFrame:
    """A chain side quoted the way Yahoo does: priced off the share price
    QUOTE_DELAY_MINUTES ago."""
    years_then = (market_data._years_to_expiration(_EXP)
                  + market_data.QUOTE_DELAY_MINUTES / (365.0 * 24 * 60))
    rows = []
    for k in strikes:
        mid = market_data.bs_price(spot_then, k, _SIGMA, years_then, option_type)
        rows.append({"strike": k, "bid": max(mid - half_spread, 0.0), "ask": mid + half_spread,
                     "lastPrice": mid, "volume": 1, "openInterest": 1})
    return pd.DataFrame(rows)


def _delayed_chain(spot_now: float, spot_then: float, strikes=(495.0, 500.0, 505.0)):
    return market_data.OptionChainSnapshot(
        ticker="SPY", expiration=_EXP, spot=spot_now,
        calls=_quoted(spot_then, strikes, "call"), puts=_quoted(spot_then, strikes, "put"))


def test_delayed_quotes_are_repriced_to_the_current_share_price():
    # the share rose 500 -> 503 in the last 15 minutes; Yahoo still quotes 500
    chain = market_data.correct_quote_delay(_delayed_chain(503.0, 500.0), 500.0)
    years = market_data._years_to_expiration(_EXP)
    atm = chain.calls[chain.calls["strike"] == 500.0].iloc[0]
    fair = market_data.bs_price(503.0, 500.0, _SIGMA, years, "call")
    assert (atm["bid"] + atm["ask"]) / 2 == pytest.approx(fair, abs=0.01)
    assert atm["ask"] - atm["bid"] == pytest.approx(0.02)          # the spread is kept
    assert atm["ask"] > atm["raw_ask"]                             # the call was quoted cheap
    put = chain.puts[chain.puts["strike"] == 500.0].iloc[0]
    assert put["ask"] < put["raw_ask"]                             # ...and the put dear
    assert chain.delay_corrected and chain.spot_at_quote == 500.0


def test_no_old_share_price_means_no_correction():
    chain = market_data.correct_quote_delay(_delayed_chain(503.0, 500.0), None)
    assert not chain.delay_corrected
    assert "raw_ask" not in chain.calls.columns


def test_deep_contracts_shift_by_intrinsic_and_never_go_negative():
    chain = _delayed_chain(503.0, 500.0, strikes=(300.0, 800.0))
    chain.calls.loc[chain.calls["strike"] == 300.0, ["bid", "ask", "lastPrice"]] = [200.0, 200.0, 200.0]
    chain.puts.loc[chain.puts["strike"] == 300.0, ["bid", "ask", "lastPrice"]] = [0.0, 0.01, 0.01]
    corrected = market_data.correct_quote_delay(chain, 500.0)
    deep_call = corrected.calls[corrected.calls["strike"] == 300.0].iloc[0]
    assert deep_call["bid"] == pytest.approx(203.0)                # intrinsic moved +3
    far_put = corrected.puts[corrected.puts["strike"] == 300.0].iloc[0]
    assert far_put["ask"] >= 0.0 and far_put["bid"] >= 0.0


def test_get_option_chain_corrects_by_default_and_raw_on_request(monkeypatch):
    calls, puts = _quoted(500.0, (500.0,), "call"), _quoted(500.0, (500.0,), "put")
    stamps = pd.date_range(end=_dt.now(_tz.utc) - _td(minutes=1), periods=40, freq="1min")
    bars = pd.DataFrame({"Close": [500.0] * 30 + [503.0] * 10}, index=stamps)   # rose 10 min ago
    fake_ticker = SimpleNamespace(option_chain=lambda exp: SimpleNamespace(calls=calls, puts=puts),
                                  history=lambda **kw: bars)
    monkeypatch.setattr(market_data.yf, "Ticker", lambda ticker: fake_ticker)
    monkeypatch.setattr(market_data, "get_current_price", lambda ticker: 503.0)
    corrected = market_data.get_option_chain("SPY", _EXP)
    assert corrected.delay_corrected and corrected.spot_at_quote == 500.0
    raw = market_data.get_option_chain("SPY", _EXP, quote_delay_minutes=0)
    assert not raw.delay_corrected
    assert raw.calls["ask"].iloc[0] == calls["ask"].iloc[0]


def test_spot_minutes_ago_reads_the_bar_that_had_closed_by_then(monkeypatch):
    now = _dt.now(_tz.utc).replace(second=0, microsecond=0)
    stamps = [now - _td(minutes=m) for m in range(30, -1, -1)]
    bars = pd.DataFrame({"Close": [float(1000 - m) for m in range(30, -1, -1)]},
                        index=pd.DatetimeIndex(stamps))
    monkeypatch.setattr(market_data.yf, "Ticker", lambda t: SimpleNamespace(history=lambda **kw: bars))
    # the bar starting 16 min ago closed 15 min ago
    assert market_data.get_spot_minutes_ago("SPY", 15) == 1000 - 16
    monkeypatch.setattr(market_data.yf, "Ticker",
                        lambda t: SimpleNamespace(history=lambda **kw: bars.iloc[-5:]))
    assert market_data.get_spot_minutes_ago("SPY", 15) is None     # nothing that old today


def test_spot_minutes_ago_ignores_a_bar_from_hours_ago(monkeypatch):
    now = _dt.now(_tz.utc)
    bars = pd.DataFrame({"Close": [767.5]}, index=pd.DatetimeIndex([now - _td(hours=5)]))
    monkeypatch.setattr(market_data.yf, "Ticker", lambda t: SimpleNamespace(history=lambda **kw: bars))
    assert market_data.get_spot_minutes_ago("SPY", 15) is None     # overnight: leave quotes alone


def test_contract_quote_raw_reads_the_quote_as_sent():
    chain = market_data.correct_quote_delay(_delayed_chain(503.0, 500.0), 500.0)
    row = chain.calls[chain.calls["strike"] == 500.0].iloc[0]
    bid, ask, _, _ = market_data.contract_quote(row, raw=True)
    assert (bid, ask) == (row["raw_bid"], row["raw_ask"])
    assert market_data.contract_quote(row)[1] == row["ask"]
