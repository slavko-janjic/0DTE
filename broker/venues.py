"""Where an order fills.

SimVenue fills both stocks and options against live quotes:
- options at the ask (buy) / bid (sell) of the real chain, repriced for
  Yahoo's quote delay exactly like the existing paper engine, plus the
  commission and exchange fees from config `costs`;
- stocks at the last price plus `broker.stock_slippage_bps` against the agent.

AlpacaVenue sends stock orders to the Alpaca paper account (keys
ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY in secrets.env) and books the price
Alpaca actually filled at. The shared Alpaca account holds the sum of every
agent's shares; each agent's slice lives in our own ledger.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from broker.models import OrderRequest
from paper_trading.costs import order_fee

ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
DEFAULT_SLIPPAGE_BPS = 5


class VenueError(RuntimeError):
    pass


@dataclass
class Fill:
    price: float
    qty: int
    venue: str
    venue_order_id: str | None = None


class SimVenue:
    name = "sim"

    def __init__(self, config: dict, stock_price=None, option_chain=None):
        from data import market_data
        self.config = config
        self.stock_price = stock_price or market_data.get_current_price
        self.option_chain = option_chain or market_data.get_option_chain
        self.slip = config.get("broker", {}).get("stock_slippage_bps", DEFAULT_SLIPPAGE_BPS) / 10000

    def _contract(self, symbol, option_type, strike, expiration):
        chain = self.option_chain(symbol, expiration)
        if chain is None or chain.expiration != expiration:
            return None
        df = chain.calls if option_type == "call" else chain.puts
        rows = df[(df["strike"] - float(strike)).abs() < 1e-6]
        return None if rows.empty else rows.iloc[0]

    def quote(self, order: OrderRequest) -> float | None:
        """The price this order would fill at right now (per share / per
        option share), or None when there's no usable quote."""
        from data import market_data
        if order.asset == "stock":
            last = self.stock_price(order.symbol)
            if not last:
                return None
            return round(last * (1 + self.slip if order.side == "buy" else 1 - self.slip), 4)
        row = self._contract(order.symbol, order.option_type, order.strike, order.expiration)
        if row is None:
            return None
        price = (market_data.contract_entry_price(row) if order.side == "buy"
                 else market_data.contract_exit_price(row))
        return round(price, 2) if price else None

    def mark(self, position) -> float | None:
        """What an open position is worth per unit now: the bid for options,
        the last price for shares."""
        from data import market_data
        if position["asset"] == "stock":
            return self.stock_price(position["symbol"])
        row = self._contract(position["symbol"], position["option_type"], position["strike"],
                             position["expiration"])
        return market_data.contract_exit_price(row) if row is not None else None

    def fees(self, order: OrderRequest, qty: int | None = None) -> float:
        if order.asset != "option":
            return 0.0
        return round(order_fee(qty or order.qty, self.config.get("costs")), 2)

    def execute(self, order: OrderRequest, price: float) -> Fill:
        return Fill(price=price, qty=order.qty, venue=self.name)


class AlpacaVenue:
    """Stock orders on the Alpaca paper account. Quotes and marks come from
    the sim venue (same live price); the fill price is Alpaca's."""
    name = "alpaca"

    def __init__(self, config: dict, sim: SimVenue | None = None, session=None,
                 poll_seconds: float = 0.5, timeout_seconds: float = 15):
        import requests
        self.sim = sim or SimVenue(config)
        self.http = session or requests.Session()
        self.http.headers.update({"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY_ID"],
                                  "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET_KEY"]})
        self.base = config.get("broker", {}).get("alpaca_url", ALPACA_PAPER_URL)
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY"))

    def quote(self, order):
        return self.sim.quote(order)

    def mark(self, position):
        return self.sim.mark(position)

    def fees(self, order, qty=None):
        return 0.0           # Alpaca charges no commission on stocks

    def execute(self, order: OrderRequest, price: float) -> Fill:
        body = {"symbol": order.symbol, "qty": str(order.qty), "side": order.side,
                "time_in_force": "day", "type": "market"}
        if order.limit_price is not None:
            body.update(type="limit", limit_price=f"{order.limit_price:.2f}")
        resp = self.http.post(f"{self.base}/v2/orders", json=body, timeout=15)
        if resp.status_code >= 300:
            raise VenueError(f"alpaca refused the order: {resp.status_code} {resp.text[:200]}")
        oid = resp.json()["id"]
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            o = self.http.get(f"{self.base}/v2/orders/{oid}", timeout=15).json()
            if o.get("status") == "filled":
                return Fill(price=float(o["filled_avg_price"]), qty=int(float(o["filled_qty"])),
                            venue=self.name, venue_order_id=oid)
            if o.get("status") in ("canceled", "expired", "rejected"):
                raise VenueError(f"alpaca order {o.get('status')}")
            if time.monotonic() > deadline:
                self.http.delete(f"{self.base}/v2/orders/{oid}", timeout=15)
                o = self.http.get(f"{self.base}/v2/orders/{oid}", timeout=15).json()
                filled = int(float(o.get("filled_qty") or 0))
                if filled:
                    return Fill(price=float(o["filled_avg_price"]), qty=filled, venue=self.name,
                                venue_order_id=oid)
                raise VenueError("alpaca order not filled in time; cancelled")
            time.sleep(self.poll_seconds)


def venue_for(order: OrderRequest, config: dict, sim: SimVenue | None = None):
    """Options always fill in the simulator (honest chain fills). Stocks go to
    Alpaca when its keys are set and config broker.stocks isn't 'sim'."""
    sim = sim or SimVenue(config)
    if (order.asset == "stock" and config.get("broker", {}).get("stocks", "alpaca") == "alpaca"
            and AlpacaVenue.available()):
        return AlpacaVenue(config, sim=sim)
    return sim
