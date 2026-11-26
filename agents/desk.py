"""Everything an LLM agent can look at or do, as plain functions.

agents/tools.py wraps these as tools for the model; tests call them
directly. Each Desk is bound to one agent and one wake-up (run).

Rules enforced here, not left to the prompt:
- an order needs a journal entry written in this same run, by this agent;
- in dry_run mode orders are checked against live quotes and the risk gate
  but never filled;
- the agent sees only its own book.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from broker import service
from broker.models import OrderRequest
from storage import agent_journal as journal
from storage import agent_ledger as ledger
from storage import digests as dstore
from storage import feeds as fstore

JOURNAL_ACTIONS = ("open", "add", "reduce", "close", "no_action", "note")
NEAR_MONEY_STRIKES = 8


def _r(x, d=2):
    try:
        return None if x is None or x != x else round(float(x), d)
    except (TypeError, ValueError):
        return None


class Desk:
    def __init__(self, db_path: str, config: dict, agent_cfg: dict, run_id: int | None, mode: str,
                 market_data=None, venue=None, market=None):
        if market_data is None:
            from data import market_data
        self.db, self.config, self.cfg = db_path, config, agent_cfg
        self.agent = agent_cfg["name"]
        self.run_id, self.mode = run_id, mode
        self.md, self.venue, self.market = market_data, venue, market
        self.orders_placed = 0
        self.deep_dives_left = int(agent_cfg.get("deep_dives_per_run", 3))

    # --- reading ----------------------------------------------------------

    def digest(self, kind: str) -> str:
        row = dstore.latest_digests(self.db).get(kind)
        if row is None:
            return f"No '{kind}' card. Cards: market, calendar, news, crowd, filings."
        return f"[{kind} card, built {row['created_at'][:16]} UTC]\n{row['content']}"

    def portfolio(self, remark: bool = True) -> dict:
        ledger.ensure_account(self.db, self.agent, self.cfg.get("starting_balance", 5000))
        pf = service.portfolio(self.db, self.config, self.agent, remark=remark, venue=self.venue)
        pf["recent_closed"] = [
            {"instrument": p["instrument"], "realized_pnl": _r(p["realized_pnl"]),
             "opened": p["opened_at"][:10], "closed": (p["closed_at"] or "")[:10]}
            for p in ledger.closed_positions(self.db, self.agent, limit=5)]
        return pf

    def quote(self, symbol: str) -> dict:
        symbol = symbol.upper()
        price = self.md.get_current_price(symbol)
        bars = self.md.get_daily_bars(symbol, period="5d")
        prev = float(bars["Close"].iloc[-2]) if bars is not None and len(bars) >= 2 else None
        return {"symbol": symbol, "price": _r(price),
                "change_pct_vs_prev_close": _r((price / prev - 1) * 100) if price and prev else None,
                "note": "delayed up to ~15 min"}

    def price_history(self, symbol: str, period: str = "6mo") -> dict:
        symbol = symbol.upper()
        if period not in ("1mo", "3mo", "6mo", "1y"):
            period = "6mo"
        bars = self.md.get_daily_bars(symbol, period=period)
        if bars is None or bars.empty:
            return {"symbol": symbol, "error": "no price history"}
        close, vol = bars["Close"], bars["Volume"]
        last = float(close.iloc[-1])

        def chg(n):
            return _r((last / float(close.iloc[-n - 1]) - 1) * 100) if len(close) > n else None

        weekly = bars.iloc[::-5].iloc[::-1]       # every 5th session, oldest first
        return {
            "symbol": symbol, "last_close": _r(last), "sessions": len(close),
            "change_pct": {"5d": chg(5), "20d": chg(20), "60d": chg(60)},
            "sma20": _r(close.tail(20).mean()), "sma50": _r(close.tail(50).mean()) if len(close) >= 50 else None,
            "high": _r(close.max()), "low": _r(close.min()),
            "avg_volume_20d": int(vol.tail(20).mean()) if len(vol) else None,
            "last_volume": int(vol.iloc[-1]),
            "weekly_closes": [[ts.date().isoformat(), _r(c)] for ts, c in weekly["Close"].items()],
        }

    def option_chain(self, symbol: str, expiration: str | None = None) -> dict:
        symbol = symbol.upper()
        exps = list(self.md.get_expirations(symbol) or [])
        if not exps:
            return {"symbol": symbol, "error": "no listed options"}
        exp = expiration if expiration in exps else exps[0]
        chain = self.md.get_option_chain(symbol, exp)
        if chain is None:
            return {"symbol": symbol, "expirations": exps[:12], "error": f"no chain for {exp}"}
        try:
            chain = self.md.enrich_with_greeks(chain)
        except Exception:  # noqa: BLE001 - greeks are a nice-to-have
            pass

        def rows(df):
            df = df.assign(dist=(df["strike"] - chain.spot).abs()).nsmallest(NEAR_MONEY_STRIKES * 2, "dist")
            out = []
            for _, r in df.sort_values("strike").iterrows():
                out.append({"strike": _r(r["strike"]), "bid": _r(r.get("bid")), "ask": _r(r.get("ask")),
                            "iv": _r(r.get("impliedVolatility"), 3), "delta": _r(r.get("delta"), 3),
                            "volume": int(r["volume"]) if r.get("volume") == r.get("volume") and r.get("volume") is not None else None,
                            "open_interest": int(r["openInterest"]) if r.get("openInterest") == r.get("openInterest") and r.get("openInterest") is not None else None})
            return out

        return {"symbol": symbol, "spot": _r(chain.spot), "expiration": exp,
                "expirations": exps[:12], "calls": rows(chain.calls), "puts": rows(chain.puts),
                "note": "buy fills at the ask, sell at the bid; quotes repriced for a ~15 min delay"}

    def ticker_buzz(self, symbol: str) -> dict:
        symbol = symbol.upper()
        hist = fstore.reddit_mention_history(self.db, symbol, "all-stocks", hours=72)
        news = [{"at": (r["published_at"] or r["first_seen"])[:16], "source": r["source"], "title": r["title"]}
                for r in fstore.recent_news_items(self.db, hours=72, limit=2000)
                if symbol in json.loads(r["tickers_json"])][:15]
        filings = [{"filed": r["filed_on"], "form": r["form"], "about": r["description"]}
                   for r in fstore.recent_filings(self.db, days=14) if r["ticker"] == symbol][:10]
        return {"symbol": symbol,
                "reddit_mentions_24h": [{"at": h["taken_at"][:16], "mentions": h["mentions"], "rank": h["rank"]}
                                        for h in hist[-8:]],
                "news": news, "sec_filings": filings}

    # --- deciding ---------------------------------------------------------

    def write_journal(self, action: str, thesis: str, ticker: str | None = None,
                      evidence: str | None = None, confidence: int | None = None,
                      target: float | None = None, stop: float | None = None,
                      hold_until: str | None = None) -> dict:
        if action not in JOURNAL_ACTIONS:
            return {"error": f"action must be one of {', '.join(JOURNAL_ACTIONS)}"}
        if not thesis or len(thesis.strip()) < 20:
            return {"error": "write the thesis in at least a sentence"}
        if action in ("open", "add") and stop is None:
            return {"error": "an open/add entry needs a stop (the price where the thesis is wrong)"}
        entry = journal.add_entry(self.db, self.agent, self.run_id, action, thesis.strip(),
                                  ticker=ticker.upper() if ticker else None, evidence=evidence,
                                  confidence=confidence, target=target, stop=stop, hold_until=hold_until)
        return {"journal_id": entry}

    def place_order(self, journal_id: int, symbol: str, side: str, qty: int, asset: str = "stock",
                    option_type: str | None = None, strike: float | None = None,
                    expiration: str | None = None, limit_price: float | None = None) -> dict:
        entry = journal.get_entry(self.db, int(journal_id))
        if entry is None or entry["agent"] != self.agent or entry["run_id"] != self.run_id:
            return {"status": "rejected", "rule": "journal_required",
                    "detail": "write_journal first in this run and pass its journal_id"}
        if entry["action"] in ("no_action", "note"):
            return {"status": "rejected", "rule": "journal_required",
                    "detail": "that journal entry says no_action/note; write an open/add/reduce/close entry"}
        order = OrderRequest(agent=self.agent, symbol=symbol, side=side, qty=int(qty), asset=asset,
                             option_type=option_type, strike=strike, expiration=expiration,
                             limit_price=limit_price, reason=entry["thesis"][:200])
        result = service.submit(self.db, self.config, order, self.cfg, venue=self.venue,
                                market=self.market, preview=self.mode != "live")
        if self.mode != "live" and result["status"] == "would_fill":
            result["status"] = "dry_run"
            result["detail"] = "dry-run mode: checked against live quotes and the risk gate, not filled"
        journal.attach_order(self.db, entry["id"], result)
        if result["status"] in ("filled", "dry_run"):
            self.orders_placed += 1
        return result

    def use_deep_dive(self) -> bool:
        if self.deep_dives_left <= 0:
            return False
        self.deep_dives_left -= 1
        return True
