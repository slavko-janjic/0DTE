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
