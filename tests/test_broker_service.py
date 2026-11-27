from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from broker import service
from broker.models import OrderRequest
from broker.risk import MarketState
from broker.venues import AlpacaVenue, Fill, SimVenue, VenueError, venue_for
from storage import agent_ledger as ledger
from storage import db as storage

TODAY = "2026-11-30"
OPEN = MarketState(open=True, today=TODAY, minutes_to_close=200)
CONFIG = {"costs": {"commission_per_contract": 0.65, "min_per_order": 1.0,
                    "exchange_fee_per_contract": 0.05},
          "market_hours": {"timezone": "America/New_York"}}


class FakeVenue:
    name = "sim"

    def __init__(self, prices, fail=False):
        self.prices, self.fail = prices, fail     # instrument -> (bid, ask)

    def quote(self, order):
        bid, ask = self.prices.get(order.instrument, (None, None))
        return ask if order.side == "buy" else bid

    def mark(self, p):
        return self.prices.get(p["instrument"], (None, None))[0]

    def fees(self, order, qty=None):
        return 1.40 if order.asset == "option" else 0.0

    def execute(self, order, price):
        if self.fail:
            raise VenueError("down")
        return Fill(price=price, qty=order.qty, venue="sim")


def make_db(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    return path


def buy_stock(path, venue, qty=5, agent="a", **kw):
    return service.submit(path, CONFIG, OrderRequest(agent, "TSLA", "buy", qty, **kw),
                          venue=venue, market=OPEN)


def test_stock_round_trip_books_cash_and_pnl(tmp_path):
    path = make_db(tmp_path)
    v = FakeVenue({"TSLA": (99.0, 100.0)})
    r = buy_stock(path, v)
    assert r["status"] == "filled" and r["cash"] == 4500.0
    v.prices["TSLA"] = (110.0, 111.0)
    pf = service.portfolio(path, CONFIG, "a", venue=v)
    assert pf["equity"] == 5050.0 and pf["positions"][0]["unrealized"] == 50.0
    r = service.submit(path, CONFIG, OrderRequest("a", "TSLA", "sell", 5), venue=v, market=OPEN)
    assert r["status"] == "filled" and r["realized"] == 50.0 and r["cash"] == 5050.0
    assert ledger.open_positions(path, "a") == []
    assert ledger.closed_positions(path, "a")[0]["realized_pnl"] == 50.0


def test_partial_sell_and_average_cost(tmp_path):
    path = make_db(tmp_path)
    v = FakeVenue({"TSLA": (99.0, 100.0)})
    buy_stock(path, v, qty=4)
    v.prices["TSLA"] = (119.0, 120.0)
    buy_stock(path, v, qty=4)          # avg (400+480)/8 = 110; value 960 < 20% of ~5080
    pos = ledger.open_positions(path, "a")[0]
    assert pos["qty"] == 8 and pos["avg_cost"] == 110.0
    r = service.submit(path, CONFIG, OrderRequest("a", "TSLA", "sell", 3), venue=v, market=OPEN)
    assert r["realized"] == 3 * (119.0 - 110.0)
    assert ledger.open_positions(path, "a")[0]["qty"] == 5


def test_option_fill_charges_fees(tmp_path):
    path = make_db(tmp_path)
    o = OrderRequest("a", "QQQ", "buy", 2, asset="option", option_type="call", strike=600,
                     expiration="2026-12-18", limit_price=2.10)
    v = FakeVenue({o.instrument: (1.90, 2.00)})
    r = service.submit(path, CONFIG, o, venue=v, market=OPEN)
    assert r["status"] == "filled" and r["price"] == 2.0 and r["fees"] == 1.40
    assert r["cash"] == 5000 - 400 - 1.40
    assert ledger.open_positions(path, "a")[0]["realized_pnl"] == -1.40


def test_rejections_are_recorded_once_for_the_agent(tmp_path):
    path = make_db(tmp_path)
    v = FakeVenue({"TSLA": (99.0, 100.0)})
    assert buy_stock(path, v, qty=20)["rule"] == "max_position_size"
    assert service.submit(path, CONFIG, OrderRequest("a", "NVDA", "buy", 1), venue=v,
                          market=OPEN)["rule"] == "no_quote"
    assert service.submit(path, CONFIG, OrderRequest("a", "TSLA", "sell", 1), venue=v,
                          market=OPEN)["rule"] == "not_held"
    assert buy_stock(path, FakeVenue({"TSLA": (99.0, 100.0)}, fail=True))["rule"] == "venue_error"
    assert service.submit(path, CONFIG, OrderRequest("a", "TSLA", "buy", 0), venue=v,
                          market=OPEN)["rule"] == "invalid"
    seen = ledger.unseen_rejections(path, "a")
    assert [r["rule"] for r in seen] == ["max_position_size", "no_quote", "not_held", "venue_error", "invalid"]
    assert ledger.unseen_rejections(path, "a") == []


def test_drawdown_pauses_the_agent(tmp_path):
    path = make_db(tmp_path)
    v = FakeVenue({"TSLA": (99.0, 100.0)})
    buy_stock(path, v, qty=9)
    ledger.update_peak(path, "a", 8000)            # pretend it once stood much higher
    r = buy_stock(path, v, qty=1)
    assert r["rule"] == "max_drawdown"
    assert ledger.get_account(path, "a")["paused"] == 1
    assert service.submit(path, CONFIG, OrderRequest("a", "TSLA", "sell", 9), venue=v,
                          market=OPEN)["status"] == "filled"


def test_settle_expired_options_at_intrinsic(tmp_path):
    path = make_db(tmp_path)
    o = OrderRequest("a", "QQQ", "buy", 1, asset="option", option_type="call", strike=600,
                     expiration=TODAY, limit_price=3.0)
    service.submit(path, CONFIG, o, venue=FakeVenue({o.instrument: (2.9, 3.0)}), market=OPEN)
    tz = ZoneInfo("America/New_York")
    assert service.settle_expired(path, CONFIG, spot_on=lambda s, d: 605.0,
                                  now=datetime(2026, 11, 30, 15, 0, tzinfo=tz)) == 0
    assert service.settle_expired(path, CONFIG, spot_on=lambda s, d: 605.0,
                                  now=datetime(2026, 11, 30, 16, 5, tzinfo=tz)) == 1
    closed = ledger.closed_positions(path, "a")[0]
    assert closed["realized_pnl"] == pytest.approx((5.0 - 3.0) * 100 - 1.40)


# --- venues -------------------------------------------------------------------

class Chain:
    def __init__(self, exp):
        self.expiration = exp
        self.calls = pd.DataFrame([{"strike": 600.0, "bid": 1.9, "ask": 2.0, "lastPrice": 1.95}])
        self.puts = pd.DataFrame([{"strike": 600.0, "bid": 0.9, "ask": 1.0, "lastPrice": 0.95}])


def test_sim_venue_quotes():
    sim = SimVenue({"costs": CONFIG["costs"], "broker": {"stock_slippage_bps": 10}},
                   stock_price=lambda s: 100.0, option_chain=lambda s, e: Chain(e))
    assert sim.quote(OrderRequest("a", "TSLA", "buy", 1)) == 100.1
    assert sim.quote(OrderRequest("a", "TSLA", "sell", 1)) == 99.9
    call = OrderRequest("a", "QQQ", "buy", 2, asset="option", option_type="call", strike=600,
                        expiration="2026-12-18", limit_price=2)
    assert sim.quote(call) == 2.0
    assert sim.quote(OrderRequest("a", "QQQ", "sell", 1, asset="option", option_type="put",
                                  strike=600, expiration="2026-12-18")) == 0.9
    assert sim.quote(OrderRequest("a", "QQQ", "buy", 1, asset="option", option_type="put",
                                  strike=601, expiration="2026-12-18")) is None
    assert sim.fees(call) == 1.40


def test_alpaca_venue_books_alpaca_fill(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")

    class Resp:
        def __init__(self, body, code=200):
            self.body, self.status_code, self.text = body, code, str(body)
        def json(self):
            return self.body

    class Session:
        headers = {}
        posted = None
        def post(self, url, json, timeout):
            Session.posted = json
            return Resp({"id": "o1"})
        def get(self, url, timeout):
            return Resp({"status": "filled", "filled_avg_price": "100.07", "filled_qty": "5"})

    v = AlpacaVenue({}, sim=SimVenue({}, stock_price=lambda s: 100.0), session=Session(), poll_seconds=0)
    fill = v.execute(OrderRequest("a", "TSLA", "buy", 5), 100.05)
    assert fill.price == 100.07 and fill.qty == 5 and fill.venue_order_id == "o1"
    assert Session.posted["type"] == "market"
    assert venue_for(OrderRequest("a", "TSLA", "buy", 1), {}).name == "alpaca"
    assert venue_for(OrderRequest("a", "TSLA", "buy", 1), {"broker": {"stocks": "sim"}}).name == "sim"
    opt = OrderRequest("a", "QQQ", "buy", 1, asset="option", option_type="call", strike=1,
                       expiration="2026-12-18", limit_price=1)
    assert venue_for(opt, {}).name == "sim"
    monkeypatch.delenv("ALPACA_API_KEY_ID")
    assert venue_for(OrderRequest("a", "TSLA", "buy", 1), {}).name == "sim"
