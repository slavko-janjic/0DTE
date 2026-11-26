"""The Desk's functions as tools the model can call (an in-process MCP
server named 'desk'). Each tool returns JSON text."""
from __future__ import annotations

import json
from typing import Annotated

from agents.desk import Desk

SERVER = "desk"


def _ok(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj, default=str) if not isinstance(obj, str) else obj}]}


def build_server(desk: Desk):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("get_digest", "Read one briefing card: market, calendar, news, crowd or filings.",
          {"kind": Annotated[str, "market | calendar | news | crowd | filings"]})
    async def get_digest(a):
        return _ok(desk.digest(a["kind"]))

    @tool("get_portfolio", "Your cash, equity, open positions with P&L, recent closed trades.", {})
    async def get_portfolio(a):
        return _ok(desk.portfolio())

    @tool("get_quote", "Latest share price of a ticker and its change vs the previous close.",
          {"symbol": str})
    async def get_quote(a):
        return _ok(desk.quote(a["symbol"]))

    @tool("get_price_history",
          "Daily price history summary: % change over 5/20/60 sessions, SMA20/50, high/low, volume, weekly closes.",
          {"symbol": str, "period": Annotated[str, "1mo | 3mo | 6mo | 1y"]})
    async def get_price_history(a):
        return _ok(desk.price_history(a["symbol"], a.get("period", "6mo")))

    @tool("get_option_chain",
          "Near-the-money calls and puts (bid, ask, IV, delta, volume, OI) for one expiration, plus the list of expirations.",
          {"symbol": str, "expiration": Annotated[str, "YYYY-MM-DD; empty for the nearest"]})
    async def get_option_chain(a):
        return _ok(desk.option_chain(a["symbol"], a.get("expiration") or None))

    @tool("get_ticker_buzz", "Reddit mention trend, recent headlines and SEC filings for one ticker.",
          {"symbol": str})
    async def get_ticker_buzz(a):
        return _ok(desk.ticker_buzz(a["symbol"]))

    @tool("write_journal",
          "Record a decision BEFORE acting on it. Required before any order. action: open | add | reduce | "
          "close | no_action | note. open/add need a stop. Returns journal_id.",
          {"type": "object", "properties": {
              "action": {"type": "string", "enum": ["open", "add", "reduce", "close", "no_action", "note"]},
              "ticker": {"type": "string"},
              "thesis": {"type": "string", "description": "why, in a few sentences"},
              "evidence": {"type": "string", "description": "the data points it rests on, with sources"},
              "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
              "target": {"type": "number"}, "stop": {"type": "number"},
              "hold_until": {"type": "string", "description": "YYYY-MM-DD planned exit"}},
           "required": ["action", "thesis"]})
    async def write_journal(a):
        return _ok(desk.write_journal(**a))

    @tool("place_order",
          "Buy or sell, referencing the journal_id of the decision. Long only: sell only what you hold. "
          "Options need a limit_price (premium per share). The risk gate may reject; read the reason.",
          {"type": "object", "properties": {
              "journal_id": {"type": "integer"},
              "symbol": {"type": "string"}, "side": {"type": "string", "enum": ["buy", "sell"]},
              "qty": {"type": "integer", "description": "shares, or option contracts"},
              "asset": {"type": "string", "enum": ["stock", "option"]},
              "option_type": {"type": "string", "enum": ["call", "put"]},
              "strike": {"type": "number"}, "expiration": {"type": "string"},
              "limit_price": {"type": "number"}},
           "required": ["journal_id", "symbol", "side", "qty"]})
    async def place_order(a):
        return _ok(desk.place_order(**a))

    tools = [get_digest, get_portfolio, get_quote, get_price_history, get_option_chain,
             get_ticker_buzz, write_journal, place_order]
    return create_sdk_mcp_server(SERVER, tools=tools), [f"mcp__{SERVER}__{t.name}" for t in tools]
