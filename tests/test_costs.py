"""Commission + fees (paper_trading.costs), how they hit paper P&L, the
research simulator's fees, the per-trade P&L breakdown, and the logged entry
context. Includes the cost-magnitude check the 2026-10-20 deep-dive asked for:
Vilkov's 0DTE repo once charged its half-spread at 1/100 of its size, and a unit
test on the size of the numbers would have caught it."""
import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import research
import worker
from analytics import option_sim
from data import market_data
from paper_trading import engine
from paper_trading.costs import order_fee, round_trip_fees
from storage import db as storage

IBKR = {"commission_per_contract": 0.65, "min_per_order": 1.00, "exchange_fee_per_contract": 0.05}


def test_fee_magnitudes():
    assert order_fee(1, IBKR) == pytest.approx(1.05)               # the $1 minimum binds
    assert round_trip_fees(1, IBKR) == pytest.approx(2.10)
    assert round_trip_fees(3, IBKR) == pytest.approx(2 * (1.95 + 0.15))   # 3 x 0.65 clears it
    assert round_trip_fees(1, IBKR, sold=False) == pytest.approx(1.05)    # expired: buy only
    assert round_trip_fees(1, None) == 0.0 and round_trip_fees(0, IBKR) == 0.0


def test_a_one_lot_spy_round_trip_costs_about_two_and_a_half_percent(tmp_path):
    # $1.23 mid, 1-cent spread: buy at 1.235, sell at 1.225 with no move
    db = str(tmp_path / "c.db")
    storage.init_db(db)
    sid = storage.open_shadow_position(db, "x", "SPY", "call", 769.0, "2099-01-02", 1.235, {})
    pnl = storage.close_shadow_position(db, sid, 1.225, "time_cutoff", IBKR)
    assert pnl == pytest.approx(-1.00 - 2.10)                         # spread + fees
    row = storage.get_closed_shadow_positions(db)[0]
    assert row["fees"] == pytest.approx(2.10)
    assert row["pnl_pct"] == pytest.approx(-3.10 / 123.5 * 100)       # about -2.5% of premium
    assert -2.6 < row["pnl_pct"] < -2.4


def test_expired_lab_position_pays_the_buy_side_only(tmp_path):
    db = str(tmp_path / "e.db")
    storage.init_db(db)
    sid = storage.open_shadow_position(db, "x", "SPY", "call", 769.0, "2099-01-02", 1.00, {})
    assert storage.close_shadow_position(db, sid, 0.0, "expired", IBKR) == pytest.approx(-100 - 1.05)


def test_real_close_credits_the_balance_net_of_fees(tmp_path):
    db = str(tmp_path / "r.db")
    storage.init_db(db)
    storage.ensure_account(db, 1000.0)
    pid = engine.buy(db, "SPY", "call", 769.0, "2099-01-02", 1.50, 2, 0.0)
    assert storage.get_balance(db) == pytest.approx(700.0)
    pnl = engine.close(db, pid, 2.00, "profit_target", IBKR)
    fees = round_trip_fees(2, IBKR)                                   # 2 x max(1.30, 1.00) + 0.20
    assert pnl == pytest.approx(100.0 - fees)
    assert storage.get_balance(db) == pytest.approx(1000.0 + 100.0 - fees)
    assert engine.close(db, pid, 2.00, "profit_target", IBKR) is None  # still single-credit


def test_simulator_takes_fees_off_the_result_not_the_exit():
    path = {m: 500.0 for m in range(0, 391)}
    cutoff = {"time_cutoff_minutes_before_close": 30}
    free = option_sim.simulate_trade(path, 30, 500.0, "call", 0.15, cutoff)
    paid = option_sim.simulate_trade(path, 30, 500.0, "call", 0.15, cutoff, fees=2.10)
    assert paid.exit_minute == free.exit_minute and paid.exit_reason == free.exit_reason
    assert free.ret_pct - paid.ret_pct == pytest.approx(2.10 / (free.entry_price * 100) * 100)


def test_pnl_breakdown_adds_up_and_points_the_right_way():
    entry, sigma = 1.235, 0.15
    years = option_sim.years_to_close(90)
    exit_mid = option_sim.bs_price(770.5, 769.0, 0.13, years, "call")
    parts = option_sim.pnl_breakdown(769.0, "call", entry, exit_mid - 0.005, 769.0, 770.5,
                                     sigma, 0.13, 30, 90, fees=2.10)
    assert parts["share_move"] > 0 and parts["time"] < 0 and parts["implied_vol"] < 0
    assert sum(parts.values()) == pytest.approx((exit_mid - 0.005 - entry) - 0.021)


def _chain(spot=769.0):
    years = market_data._years_to_expiration("2099-01-02")
    rows = lambda kind: pd.DataFrame([{"strike": k, "bid": p - 0.005, "ask": p + 0.005, "lastPrice": p}
                                      for k in (768.0, 769.0, 770.0)
                                      for p in [market_data.bs_price(spot, k, 0.15, years, kind)]])
    return market_data.OptionChainSnapshot("SPY", "2099-01-02", spot, rows("call"), rows("put"))


def test_contract_iv_and_implied_move_read_the_chain():
    chain = _chain()
    row = chain.calls[chain.calls["strike"] == 769.0].iloc[0]
    assert market_data.contract_iv(chain, row, "call") == pytest.approx(0.15, abs=0.002)
    put = chain.puts[chain.puts["strike"] == 769.0].iloc[0]
    move = market_data.implied_move_pct(chain, 769.0)
    assert move == pytest.approx(((row["bid"] + row["ask"]) / 2 + (put["bid"] + put["ask"]) / 2)
                                 / 769.0 * 100)


def test_entry_context_logs_iv_move_and_fed_day(monkeypatch):
    chain = _chain()
    contract = chain.calls[chain.calls["strike"] == 769.0].iloc[0]
    today = date(2026, 10, 28)
    worker._day_open_cache.clear()
    bars = pd.DataFrame({"Open": [765.0]}, index=pd.DatetimeIndex([pd.Timestamp(today)]))
    monkeypatch.setattr(worker.market_data, "get_daily_bars", lambda t, period="1d": bars)
    config = {"market_hours": {"timezone": "America/New_York"},
              "market_catalysts": [{"date": "2026-10-28", "time": "14:00", "label": "FOMC",
                                    "impact": "high"}]}
    ctx = worker.entry_context(config, "SPY", chain, contract, "call", today)
    assert ctx["spot"] == 769.0 and ctx["iv"] == pytest.approx(0.15, abs=0.002)
    assert ctx["morning_move_pct"] == pytest.approx(4 / 765 * 100, rel=1e-3)
    assert ctx["morning_vs_implied"] > 0 and ctx["fomc"] is True


def test_lab_breakdown_reports_parts_and_splits(tmp_path):
    db = str(tmp_path / "l.db")
    storage.init_db(db)
    info = {"spot": 769.0, "iv": 0.15, "minutes_since_open": 30.0, "fomc": False}
    sid = storage.open_shadow_position(db, "morning_orb", "SPY", "call", 769.0, "2099-01-02",
                                       1.235, info)
    storage.close_shadow_position(db, sid, 1.60, "trailing_stop", IBKR,
                                  {"spot": 770.2, "iv": 0.14, "minutes_since_open": 70.0})
    rows = storage.get_closed_shadow_positions(db)
    from zoneinfo import ZoneInfo
    stats = research.lab_breakdown(rows, date(2020, 1, 1), ZoneInfo("America/New_York"))
    s = stats["morning_orb"]
    assert s["n"] == 1 and len(s["parts"]) == 1 and len(s["call"]) == 1 and len(s["other_days"]) == 1
    p = s["parts"][0]
    assert sum(p.values()) == pytest.approx(rows[0]["pnl_pct"], abs=1e-6)  # parts sum to net %
    assert p["fees"] == pytest.approx(-2.10 / 123.5 * 100)
