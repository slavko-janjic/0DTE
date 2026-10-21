"""share_lab (the morning signals on the share itself) and the Fed-day ramp
strategies in the lab."""
from datetime import date
from types import SimpleNamespace

import pytest

import research
import worker
from analytics import option_sim, share_lab
from analytics import session_lab as lab
from storage import db as storage


def test_barriers_are_where_the_option_hits_its_exits():
    up, down = share_lab.option_barriers(769.0, 0.15, 30, "call")
    years = option_sim.years_to_close(30)
    price = option_sim.bs_price(769.0, 769.0, 0.15, years, "call")
    assert option_sim.bs_price(up, 769.0, 0.15, years, "call") == pytest.approx(price * 1.5, rel=1e-4)
    assert option_sim.bs_price(down, 769.0, 0.15, years, "call") == pytest.approx(price * 0.65, rel=1e-4)
    assert down < 769.0 < up
    pu, pd = share_lab.option_barriers(769.0, 0.15, 30, "put")
    assert pu < 769.0 < pd                       # a put's target is a lower share price


def test_share_trade_exits_at_the_first_level_or_the_cutoff():
    path = {m: 100.0 for m in range(0, 391)}
    for m in range(40, 391):
        path[m] = 100.3                           # +30 bps at 40
    assert share_lab.share_trade(path, 30, 1, 100.2, 99.9) == pytest.approx(30.0)
    assert share_lab.share_trade(path, 30, -1, 99.8, 100.1) == pytest.approx(-30.0)   # short stopped
    assert share_lab.share_trade(path, 30, 1, None, None) == pytest.approx(30.0)      # hold to cutoff


def test_label_is_long_and_short_net_of_costs():
    s = lab.Session(date(2024, 2, 1))
    for m in range(390):
        price = 100.0 if m < 40 else 100.5
        s.bars[m] = lab.Bar(price, price, price, price, 100.0)
    label = share_lab.label_session(s, 0.15, 30)
    assert label["call"] > 0 > label["put"]
    up, down = share_lab.option_barriers(100.0, 0.15, 30, "call")
    gross_long = share_lab.share_trade(s.path(), 30, 1, up, down)
    assert label["call"] == pytest.approx(gross_long - share_lab.ROUND_TRIP_BPS)   # cost comes off


def test_fed_ramp_strategies_enter_only_on_fed_days(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fomc.db")
    storage.init_db(db_path)
    monkeypatch.setattr(worker, "minutes_since_market_open", lambda config: 31.0)
    monkeypatch.setattr(worker, "minutes_to_market_close", lambda config: 359.0)
    monkeypatch.setattr(worker, "is_market_open", lambda config: True)
    monkeypatch.setattr(worker.market_data, "find_strike_contract",
                        lambda df, spot, kind, steps=0: {"lastPrice": 2.0, "strike": 500.0,
                                                         "bid": 1.9, "ask": 2.1})
    entry = {"direction_rule": "always_call", "only_on": "fomc", "min_confidence_pct": 0,
             "window_start_minutes": 30, "window_end_minutes": 36, "no_entry_last_minutes": 60}
    config = {"market_hours": {"open": "09:30", "close": "16:00", "timezone": "America/New_York"},
              "market_holidays": [], "market_half_days": [], "tickers": ["QQQ"],
              "market_catalysts": [],
              "shadow_strategies": [{"name": "fomc_ramp_call", "entry": entry,
                                     "exit": {"time_cutoff_minutes_before_close": 125}}]}
    chain = SimpleNamespace(calls="C", puts="P", spot=500.0, expiration="2099-01-02")
    signal = SimpleNamespace(direction="neutral", confidence_pct=0.0, composite_score=0.0,
                             subscores_used={})
    worker.process_shadow_strategies("QQQ", config, db_path, chain, signal, None)
    assert storage.get_open_shadow_positions(db_path, "QQQ") == []          # not a Fed day
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    config["market_catalysts"] = [{"date": today, "time": "14:00", "label": "FOMC", "impact": "high"}]
    worker.process_shadow_strategies("QQQ", config, db_path, chain, signal, None)
    assert [r["strategy"] for r in storage.get_open_shadow_positions(db_path, "QQQ")] == ["fomc_ramp_call"]


def test_fed_ramp_readout_adds_the_two_legs(tmp_path):
    db = str(tmp_path / "ramp.db")
    storage.init_db(db)
    for name, kind, entry, exit_ in (("fomc_ramp_call", "call", 1.20, 1.50),
                                     ("fomc_ramp_put", "put", 1.10, 0.90)):
        sid = storage.open_shadow_position(db, name, "SPY", kind, 769.0, "2099-01-02", entry, {})
        storage.close_shadow_position(db, sid, exit_, "time_cutoff")
    from zoneinfo import ZoneInfo
    rows = research.fed_ramp(storage.get_closed_shadow_positions(db), ZoneInfo("America/New_York"))
    assert len(rows) == 1
    assert rows[0]["pnl"] == pytest.approx(30.0 - 20.0)
    assert rows[0]["pct"] == pytest.approx(10.0 / 230.0 * 100)
