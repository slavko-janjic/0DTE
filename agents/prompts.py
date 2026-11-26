"""The prompts an agent wakes up to: a fixed system prompt (shared rules +
its persona) and a briefing with everything it needs to start."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker import risk
from storage import agent_journal as journal
from storage import agent_ledger as ledger
from storage import digests as dstore

PERSONA_DIR = Path(__file__).resolve().parent / "personas"

RULES = """You are {label}, one of several autonomous trading agents. Each agent runs its own
${start:,.0f} paper account; the results are compared, so judgment and discipline matter
more than activity.

How this works
- You wake up on a schedule. Each wake-up you get a briefing (below in the user message),
  can use tools to look deeper, and then either act or stand aside.
- Before ANY order, call write_journal with your decision: the thesis, the evidence it rests
  on (numbers, sources), confidence 0-100, a stop and a target for opens/adds, and a planned
  hold date. Then place_order with that journal_id. If you decide not to trade, write one
  no_action entry saying why, briefly.
- Orders are long only: buy shares or options; sell only what you hold. Options need a
  limit price (premium per share; 1 contract = 100 shares). Buys fill at the ask, sells at
  the bid, quotes are delayed up to ~15 min.
- A risk gate checks every order and may reject it; the reason comes back. These limits
  are fixed: {limits}. Don't argue with a rejection; adjust or stand aside.
- WebSearch is available for up to {dives} searches this wake-up, for checking something
  specific. Don't browse for ideas; the briefing cards are your feed.
- Use only data you were given or fetched. Never invent prices, news or numbers.
- Finish with ONE line summarizing what you did and why (it is logged).

Your style
{persona}
"""


def system_prompt(config: dict, agent_cfg: dict) -> str:
    persona_file = PERSONA_DIR / agent_cfg.get("persona", f"{agent_cfg['name']}.md")
    persona = persona_file.read_text(encoding="utf-8") if persona_file.exists() else agent_cfg.get("about", "")
    lim = risk.effective_limits(config, agent_cfg)
    limits = (f"one position at most {lim['max_position_pct']:g}% of equity; 0DTE premium at most "
              f"{lim['max_0dte_premium_pct']:g}%; no new buys after a {lim['max_daily_loss_pct']:g}% day loss; "
              f"paused at a {lim['max_drawdown_pct']:g}% drawdown; at most {lim['max_open_positions']} "
              f"positions; assets: {', '.join(lim['allowed_assets'])}")
    return RULES.format(label=agent_cfg.get("label", agent_cfg["name"]),
                        start=agent_cfg.get("starting_balance", 5000), limits=limits,
                        dives=agent_cfg.get("deep_dives_per_run", 3), persona=persona.strip())


def briefing(db_path: str, config: dict, agent_cfg: dict, portfolio: dict, market, mode: str,
             now: datetime | None = None) -> str:
    tz = ZoneInfo(config.get("market_hours", {}).get("timezone", "America/New_York"))
    now = (now or datetime.now(tz)).astimezone(tz)
    agent = agent_cfg["name"]
    parts = [f"Now: {now:%A %Y-%m-%d %H:%M} New York time. Market "
             + (f"OPEN, {market.minutes_to_close:.0f} min to the close." if market.open else "CLOSED: orders will be rejected.")]
    if mode != "live":
        parts.append("MODE: DRY RUN. Orders are checked against live quotes and the risk gate but not filled. "
                     "Decide exactly as you would for real.")
    parts.append("YOUR BOOK\n" + json.dumps(portfolio, default=str))
    rejected = ledger.unseen_rejections(db_path, agent)
    if rejected:
        parts.append("REJECTED SINCE YOUR LAST WAKE-UP\n" + "\n".join(
            f"- {r['at'][:16]} {r['rule']}: {r['detail']}" for r in rejected))
    entries = journal.recent_entries(db_path, agent, limit=6)
    if entries:
        parts.append("YOUR RECENT JOURNAL (newest first)\n" + "\n".join(
            f"- {e['at'][:16]} {e['action']} {e['ticker'] or ''}: {e['thesis'][:300]}"
            + (f" [stop {e['stop']}, target {e['target']}, hold until {e['hold_until']}]" if e['stop'] else "")
            for e in entries))
    cards = dstore.latest_digests(db_path)
    order = agent_cfg.get("digests", ["market", "calendar", "news", "crowd", "filings"])
    for kind in order:
        if kind in cards:
            parts.append(f"BRIEFING CARD: {kind} (built {cards[kind]['created_at'][:16]} UTC)\n{cards[kind]['content']}")
    watch = config.get("feeds", {}).get("watchlist", [])
    parts.append("Watchlist with news and filings coverage: " + ", ".join(watch)
                 + ". You may trade any liquid US stock or ETF.")
    parts.append("Start with your open positions, then decide. Journal first, then orders. End with one summary line.")
    return "\n\n".join(parts)
