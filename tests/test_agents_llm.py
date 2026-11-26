from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import agent_runner
from agents import prompts
from agents.desk import Desk
from agents.runner import run_agent
from broker.risk import MarketState
from broker.venues import Fill
from storage import agent_journal as journal
from storage import agent_ledger as ledger
from storage import db as storage
from storage import digests as dstore
from storage import feeds as fstore

OPEN = MarketState(open=True, today="2026-11-30", minutes_to_close=200)
AGENT = {"name": "momentum", "label": "Momentum swing", "starting_balance": 5000,
         "persona": "momentum.md", "deep_dives_per_run": 2}
CONFIG = {"market_hours": {"timezone": "America/New_York"}, "feeds": {"watchlist": ["QQQ", "TSLA"]}}


class FakeVenue:
    name = "sim"
    def quote(self, o): return 100.0
    def mark(self, p): return 100.0
    def fees(self, o, qty=None): return 0.0
    def execute(self, o, price): return Fill(price=price, qty=o.qty, venue="sim")


class FakeMD:
    def get_current_price(self, s): return 110.0
    def get_daily_bars(self, s, period="5d"):
        idx = pd.date_range("2026-06-01", periods=120, freq="B")
        return pd.DataFrame({"Close": [50 + i * 0.5 for i in range(120)], "Volume": [1000] * 120}, index=idx)
    def get_expirations(self, s): return ["2026-12-18", "2027-01-15"]
    def get_option_chain(self, s, e):
        df = pd.DataFrame([{"strike": k, "bid": 1.0, "ask": 1.1, "impliedVolatility": 0.4,
                            "volume": 5, "openInterest": 50} for k in range(80, 141, 5)])
        return SimpleNamespace(spot=110.0, expiration=e, calls=df, puts=df)
    def enrich_with_greeks(self, c): return c


def make_db(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    return path


def desk(path, mode="live", run_id=None):
    run_id = run_id or journal.start_run(path, "momentum", mode)
    return Desk(path, CONFIG, AGENT, run_id, mode, market_data=FakeMD(), venue=FakeVenue(), market=OPEN)


def test_order_needs_journal_from_this_run(tmp_path):
    path = make_db(tmp_path)
    d = desk(path)
    assert d.place_order(999, "TSLA", "buy", 1)["rule"] == "journal_required"
    assert "error" in d.write_journal("open", "too short")
    assert "stop" in d.write_journal("open", "TSLA is trending up on volume, above both averages.",
                                     ticker="TSLA")["error"]
    jid = d.write_journal("open", "TSLA is trending up on volume, above both averages.", ticker="tsla",
                          stop=95, target=120, confidence=60)["journal_id"]
    other = desk(path)                       # a later wake-up can't reuse it
    assert other.place_order(jid, "TSLA", "buy", 1)["rule"] == "journal_required"
    r = d.place_order(jid, "TSLA", "buy", 5)
    assert r["status"] == "filled" and d.orders_placed == 1
    assert ledger.open_positions(path, "momentum")[0]["qty"] == 5
    assert '"filled"' in journal.get_entry(path, jid)["orders_json"]
    nid = d.write_journal("no_action", "Nothing else meets the trend criteria today.")["journal_id"]
    assert d.place_order(nid, "TSLA", "buy", 1)["rule"] == "journal_required"


def test_dry_run_checks_but_never_fills(tmp_path):
    path = make_db(tmp_path)
    d = desk(path, mode="dry_run")
    jid = d.write_journal("open", "QQQ trend intact, buying a small starter position.", ticker="QQQ",
                          stop=90)["journal_id"]
    r = d.place_order(jid, "QQQ", "buy", 5)
    assert r["status"] == "dry_run" and r["price"] == 100.0
    assert d.place_order(jid, "QQQ", "buy", 50)["rule"] == "max_position_size"
    assert ledger.open_positions(path, "momentum") == []
    assert ledger.unseen_rejections(path, "momentum") == []     # previews log nothing


def test_reading_tools(tmp_path):
    path = make_db(tmp_path)
    d = desk(path)
    assert d.quote("tsla")["price"] == 110.0
    h = d.price_history("TSLA", "6mo")
    assert h["last_close"] == 109.5 and h["sma50"] is not None and h["change_pct"]["5d"] > 0
    oc = d.option_chain("TSLA")
    assert oc["expiration"] == "2026-12-18" and len(oc["calls"]) == 13
    assert "No 'market' card" in d.digest("market")
    dstore.insert_digest(path, "market", "**Calm.**")
    assert "**Calm.**" in d.digest("market")
    fstore.insert_news_items(path, [{"id": "1", "source": "s", "title": "Tesla up",
                                     "published_at": datetime.now(timezone.utc).isoformat(),
                                     "tickers": ["TSLA"]}])
    assert d.ticker_buzz("TSLA")["news"][0]["title"] == "Tesla up"
    assert d.use_deep_dive() and d.use_deep_dive() and not d.use_deep_dive()


def test_prompts(tmp_path):
    path = make_db(tmp_path)
    sp = prompts.system_prompt({}, AGENT)
    assert "Momentum swing" in sp and "$5,000" in sp and "buy strength" in sp and "20% of equity" in sp
    ledger.record_rejection(path, "momentum", "max_position_size", "too big", {})
    dstore.insert_digest(path, "news", "**Fed holds.**")
    b = prompts.briefing(path, CONFIG, AGENT, {"cash": 5000}, OPEN, "dry_run",
                         now=datetime(2026, 11, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert "Monday 2026-11-30 10:00" in b and "DRY RUN" in b and "max_position_size: too big" in b
    assert "BRIEFING CARD: news" in b and "QQQ, TSLA" in b


def test_run_agent_records_run_and_usage(tmp_path, monkeypatch):
    path = make_db(tmp_path)

    async def fake_drive(desk_obj, system, prompt, model, max_turns, timeout):
        jid = desk_obj.write_journal("no_action", "No setup met the trend rules this morning.")["journal_id"]
        assert jid
        res = SimpleNamespace(is_error=False, result="Stood aside: nothing trending cleanly.",
                              usage={"input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 500},
                              total_cost_usd=0.05, duration_ms=30000, num_turns=4, subtype="success")
        return res, []

    monkeypatch.setattr("agents.desk.Desk.portfolio", lambda self, remark=True: {"cash": 5000})
    out = run_agent(path, CONFIG, AGENT, mode="dry_run", market=OPEN, drive=fake_drive)
    assert out["status"] == "ok" and out["tokens_in"] == 10000
    run = journal.recent_runs(path, "momentum")[0]
    assert run["summary"] == "Stood aside: nothing trending cleanly." and run["turns"] == 4
    assert dstore.usage_summary(path)["agent:momentum"]["calls"] == 1

    async def broken(*a):
        raise RuntimeError("cli missing")
    assert run_agent(path, CONFIG, AGENT, mode="dry_run", market=OPEN, drive=broken)["status"] == "error"


def test_due_agents_schedule_mode_and_cap(tmp_path):
    path = make_db(tmp_path)
    ny = ZoneInfo("America/New_York")
    cfg = {"market_hours": {"timezone": "America/New_York"}, "llm_agents": [
        {"name": "a", "at": ["10:00"], "mode": "dry_run"},
        {"name": "b", "at": ["10:00"], "mode": "off"},
        {"name": "c", "at": ["10:00"], "max_tokens_per_day": 100}]}
    now = datetime(2026, 11, 30, 10, 5, tzinfo=ny)
    assert [a["name"] for a in agent_runner.due_agents(path, cfg, now)] == ["a", "c"]
    rid = journal.start_run(path, "c", "dry_run")
    journal.finish_run(path, rid, "ok", tokens_in=150)
    real_now = datetime.now(timezone.utc)
    assert agent_runner.over_token_cap(path, cfg, cfg["llm_agents"][2], real_now)
    assert not agent_runner.over_token_cap(path, cfg, cfg["llm_agents"][0], real_now)


def test_agents_page_payload(tmp_path):
    from webapi.llm_agents import agents_payload
    path = make_db(tmp_path)
    cfg = {**CONFIG, "llm_agents": [AGENT | {"mode": "dry_run", "at": ["09:50"]}]}
    assert agents_payload(path, cfg)["agents"][0]["book"] is None
    d = desk(path)
    jid = d.write_journal("open", "TSLA is trending up on volume, above both averages.", ticker="TSLA",
                          stop=95)["journal_id"]
    d.place_order(jid, "TSLA", "buy", 5)
    a = agents_payload(path, cfg)["agents"][0]
    assert a["book"]["positions"][0]["instrument"] == "TSLA"
    assert a["journal"][0]["orders"][0]["status"] == "filled"
    assert a["runs"][0]["status"] == "running" and a["schedule"] == ["09:50"]
