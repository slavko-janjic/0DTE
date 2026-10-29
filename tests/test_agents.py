"""Agents: lab strategies trading their own paper money, sized and guarded like
the autopilot, without touching the real account."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import worker
from paper_trading.shadow import agent_book, agent_strategies
from storage import db as storage
from webapi import payloads

ET = ZoneInfo("America/New_York")
ENTRY = {"direction_rule": "always_call", "min_confidence_pct": 0,
         "window_start_minutes": 30, "window_end_minutes": 36, "no_entry_last_minutes": 60}
EXIT = {"profit_target_pct": 50, "stop_loss_pct": -35, "time_cutoff_minutes_before_close": 30}


def _config(**agent):
    return {"market_hours": {"open": "09:30", "close": "16:00", "timezone": "America/New_York"},
            "market_holidays": [], "market_half_days": [], "tickers": ["QQQ"],
            "market_catalysts": [], "account": {"starting_balance": 10000},
            "autopilot": {"risk_per_trade_pct": 5, "daily_loss_limit_pct": 10,
                          "no_entry_before_catalyst_minutes": 15,
                          "close_before_catalyst_minutes": 5},
            "shadow_strategies": [{"name": "follow_me", "entry": ENTRY, "exit": EXIT}],
            "agents": [{"name": "agent_a", "follows": "follow_me", "starting_balance": 10000,
                        "risk_per_trade_pct": 5, **agent}]}


@pytest.fixture
def market(tmp_path, monkeypatch):
    db_path = str(tmp_path / "agents.db")
    storage.init_db(db_path)
    storage.ensure_account(db_path, 10000)
    monkeypatch.setattr(worker, "minutes_since_market_open", lambda config: 31.0)
    monkeypatch.setattr(worker, "minutes_to_market_close", lambda config: 359.0)
    monkeypatch.setattr(worker, "is_market_open", lambda config: True)
    monkeypatch.setattr(worker.market_data, "find_strike_contract",
                        lambda df, spot, kind, steps=0: {"lastPrice": 2.0, "strike": 500.0,
                                                         "bid": 2.0, "ask": 2.0})
    monkeypatch.setattr(worker.market_data, "contract_entry_price", lambda contract: 2.0)
    return db_path


def _run(db_path, config):
    chain = SimpleNamespace(calls="C", puts="P", spot=500.0, expiration="2099-01-02")
    signal = SimpleNamespace(direction="neutral", confidence_pct=0.0, composite_score=0.0,
                             subscores_used={})
    worker.process_shadow_strategies("QQQ", config, db_path, chain, signal, None)


def _agent_rows(db_path):
    return [r for r in storage.get_open_shadow_positions(db_path) if r["strategy"] == "agent_a"]


def test_an_agent_takes_its_strategys_rules_with_its_own_overrides():
    config = _config(entry={"window_start_minutes": 61})
    [agent] = agent_strategies(config)
    assert agent["name"] == "agent_a"
    assert agent["entry"]["window_start_minutes"] == 61                # overridden
    assert agent["entry"]["direction_rule"] == "always_call"           # inherited
    assert agent["exit"] == EXIT
    config["agents"].append({"name": "orphan", "follows": "not_configured"})
    assert [a["name"] for a in agent_strategies(config)] == ["agent_a"]  # nothing to follow


def test_book_is_starting_balance_plus_realized_minus_premium_tied_up():
    today = datetime(2026, 10, 22, tzinfo=ET).date()
    now = datetime(2026, 10, 22, 15, 0, tzinfo=ET).astimezone(ZoneInfo("UTC")).isoformat()
    earlier = datetime(2026, 10, 21, 15, 0, tzinfo=ET).astimezone(ZoneInfo("UTC")).isoformat()
    rows = [{"status": "closed", "pnl": 300.0, "exit_time": earlier, "entry_price": 2.0, "contracts": 2},
            {"status": "closed", "pnl": -100.0, "exit_time": now, "entry_price": 2.0, "contracts": 2},
            {"status": "open", "pnl": None, "exit_time": None, "entry_price": 1.5, "contracts": 2}]
    book = agent_book(rows, 10000, today, ET)
    assert book["cash"] == pytest.approx(10000 + 200 - 300)
    assert book["realized_today"] == pytest.approx(-100)
    assert book["equity_at_open"] == pytest.approx(10300)


def test_agent_sizes_from_its_own_cash_and_leaves_the_real_account_alone(market):
    _run(market, _config())
    [row] = _agent_rows(market)
    assert row["contracts"] == 2                   # 5% of $10,000 = $500 -> 2 x $200
    lab = [r for r in storage.get_open_shadow_positions(market) if r["strategy"] == "follow_me"]
    assert lab[0]["contracts"] == 1                # the lab strategy itself stays 1-lot
    assert storage.get_balance(market) == 10000
    assert storage.get_open_positions(market) == []


def test_agent_with_too_little_cash_stands_down(market):
    _run(market, _config(starting_balance=3000))   # 5% = $150 < one $200 contract
    assert _agent_rows(market) == []


def test_agent_stops_for_the_day_at_its_loss_limit(market):
    sid = storage.open_shadow_position(market, "agent_a", "SPY", "call", 500.0, "2099-01-02",
                                       10.0, {}, contracts=2)
    storage.close_shadow_position(market, sid, 4.5, "stop_loss")   # -$1,100 today, > 10%
    _run(market, _config())
    assert _agent_rows(market) == []


def test_agent_stands_down_just_before_a_catalyst(market):
    soon = datetime.now(ET) + timedelta(minutes=10)
    config = _config()
    config["market_catalysts"] = [{"date": soon.date().isoformat(), "time": soon.strftime("%H:%M"),
                                   "label": "CPI", "impact": "high"}]
    contracts, blocker = worker.agent_entry(config, market, config["agents"][0], 2.0,
                                            soon - timedelta(minutes=10))
    assert contracts == 0 and "catalyst" in blocker


def test_agent_never_holds_into_a_catalyst(market, monkeypatch):
    sid = storage.open_shadow_position(market, "agent_a", "QQQ", "call", 500.0, "2099-01-02",
                                       2.0, {}, contracts=2)
    monkeypatch.setattr(worker.market_data, "find_contract_price", lambda *a: 2.1)
    monkeypatch.setattr(worker, "_catalyst_imminent", lambda config: True)
    _run(market, _config())
    closed = {r["id"]: r for r in storage.get_closed_shadow_positions(market)}
    assert closed[sid]["exit_reason"] == "catalyst"


def test_agents_payload_reports_each_book(market):
    sid = storage.open_shadow_position(market, "agent_a", "QQQ", "call", 500.0, "2099-01-02",
                                       2.0, {}, contracts=2)
    storage.close_shadow_position(market, sid, 3.0, "profit_target",
                                  {"commission_per_contract": 0.65, "min_per_order": 1.0,
                                   "exchange_fee_per_contract": 0.05})
    out = payloads.agents_payload(market, _config(label="Agent A"))
    [agent] = out["agents"]
    fees = 200.0 - agent["realized"]
    assert agent["label"] == "Agent A" and agent["trades"] == 1
    assert 0 < fees < 10                                       # commission came off
    assert agent["equity"] == pytest.approx(10000 + agent["realized"])
    assert agent["return_pct"] == pytest.approx(agent["realized"] / 100)
    assert out["autopilot"]["trades"] == 0 and out["since"] is not None


def test_a_straddle_agent_runs_both_legs_on_one_book(market):
    config = _config()
    config["shadow_strategies"].append({"name": "put_leg", "exit": EXIT,
                                        "entry": dict(ENTRY, direction_rule="always_put")})
    config["agents"] = [{"name": "agent_s", "follows": ["follow_me", "put_leg"],
                         "starting_balance": 10000, "risk_per_trade_pct": 5}]
    assert [s["name"] for s in agent_strategies(config)] == ["agent_s.follow_me", "agent_s.put_leg"]
    _run(market, config)
    legs = {r["strategy"]: r for r in storage.get_open_shadow_positions(market)
            if r["strategy"].startswith("agent_s.")}
    assert legs["agent_s.follow_me"]["option_type"] == "call"
    assert legs["agent_s.put_leg"]["option_type"] == "put"
    # the put was sized from what the call left: $9,600 * 5% = $480 -> 2 x $200
    assert legs["agent_s.put_leg"]["contracts"] == 2
    [agent] = payloads.agents_payload(market, config)["agents"]
    assert agent["cash"] == pytest.approx(10000 - 800)
    assert agent["follows"] == "follow_me + put_leg"
    assert storage.get_strategy_shadow_positions(market, "agent") == []   # prefix is per leg
