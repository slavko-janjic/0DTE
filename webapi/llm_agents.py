"""Payload for the Agents page: one card per LLM agent (its face settings,
mood, headline stats), a trade list across all of them, and each agent's
detail (positions, journal, wake-ups, rejections). Reads stored marks only
(no live quotes), so the page is instant; agents re-mark their books every
wake-up."""
from __future__ import annotations

import json

from broker import service
from storage import agent_journal as journal
from storage import agent_ledger as ledger

# A starting look per persona; config `avatar:` overrides any of it.
DEFAULT_AVATARS = {
    "momentum": {"color": "#f59e0b", "eyes": "sharp", "accessory": "visor"},
    "contrarian": {"color": "#8b5cf6", "eyes": "squint", "accessory": "brow"},
    "fundamentals": {"color": "#10b981", "eyes": "round", "accessory": "glasses"},
    "event": {"color": "#3b82f6", "eyes": "round", "accessory": "headset"},
    "gamma": {"color": "#ef4444", "eyes": "sharp", "accessory": "goggles"},
}
FALLBACK_COLORS = ["#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1"]


def avatar_for(cfg: dict, index: int) -> dict:
    base = dict(DEFAULT_AVATARS.get(cfg["name"], {"color": FALLBACK_COLORS[index % len(FALLBACK_COLORS)],
                                                   "eyes": "round", "accessory": "none"}))
    base.update(cfg.get("avatar") or {})
    return base


def mood(cfg: dict, book: dict | None) -> str:
    """What the face shows: asleep when paused or off, otherwise today's direction."""
    if cfg.get("mode") == "off" or (book and book.get("paused")):
        return "asleep"
    today = (book or {}).get("today_pct")
    if today is None or abs(today) < 0.1:
        return "neutral"
    return "happy" if today > 0 else "sad"


def _trades(db_path: str, agent: str) -> list[dict]:
    """Orders that filled or would have filled (dry run), from the journal so
    each carries its reason, plus option expiries (which have no journal)."""
    out = []
    for e in journal.recent_entries(db_path, agent, limit=200):
        for o in json.loads(e["orders_json"]) if e["orders_json"] else []:
            if o.get("status") not in ("filled", "dry_run"):
                continue
            out.append({"agent": agent, "at": e["at"], "side": o.get("side"), "qty": o.get("qty"),
                        "instrument": o.get("instrument"), "price": o.get("price"),
                        "realized": o.get("realized"), "dry_run": o.get("status") == "dry_run",
                        "reason": e["thesis"], "action": e["action"]})
    for f in ledger.fills(db_path, agent, limit=200):
        if f["side"] == "expire":
            pos = next((p for p in ledger.closed_positions(db_path, agent, limit=500)
                        if p["id"] == f["position_id"]), None)
            out.append({"agent": agent, "at": f["at"], "side": "expire", "qty": f["qty"],
                        "instrument": pos["instrument"] if pos else "option", "price": f["price"],
                        "realized": f["realized"], "dry_run": False, "reason": f["reason"],
                        "action": "expire"})
    return out


def _stats(db_path: str, agent: str, book: dict | None, trades: list[dict]) -> dict:
    closed = ledger.closed_positions(db_path, agent, limit=1000)
    pnls = [p["realized_pnl"] for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    runs = journal.recent_runs(db_path, agent, limit=1)
    return {
        "equity": book["equity"] if book else None,
        "return_pct": book["return_pct"] if book else 0.0,
        "today_pct": book["today_pct"] if book else None,
        "drawdown_pct": book["drawdown_pct"] if book else None,
        "open_positions": len(book["positions"]) if book else 0,
        "trades": sum(1 for t in trades if not t["dry_run"]),
        "dry_trades": sum(1 for t in trades if t["dry_run"]),
        "closed": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "last_wake": runs[0]["started_at"] if runs else None,
    }


def agents_payload(db_path: str, config: dict) -> dict:
    agents, all_trades = [], []
    for i, cfg in enumerate(a for a in (config.get("llm_agents") or []) if a.get("name")):
        name = cfg["name"]
        book = service.portfolio(db_path, config, name, remark=False) if ledger.get_account(db_path, name) else None
        trades = _trades(db_path, name)
        all_trades.extend(trades)
        runs = [{"started_at": r["started_at"], "mode": r["mode"], "status": r["status"],
                 "summary": r["summary"], "orders": r["orders"], "turns": r["turns"],
                 "tokens": (r["tokens_in"] or 0) + (r["tokens_out"] or 0), "error": r["error"]}
                for r in journal.recent_runs(db_path, name, limit=8)]
        entries = [{"at": e["at"], "action": e["action"], "ticker": e["ticker"], "thesis": e["thesis"],
                    "evidence": e["evidence"], "confidence": e["confidence"], "stop": e["stop"],
                    "target": e["target"], "hold_until": e["hold_until"],
                    "orders": json.loads(e["orders_json"]) if e["orders_json"] else []}
                   for e in journal.recent_entries(db_path, name, limit=12)]
        rejected = [{"at": r["at"], "rule": r["rule"], "detail": r["detail"]}
                    for r in ledger.recent_rejections(db_path, name, limit=8)]
        agents.append({
            "name": name, "label": cfg.get("label", name), "about": cfg.get("about"),
            "mode": cfg.get("mode", "dry_run"), "model": cfg.get("model", "sonnet"),
            "schedule": cfg.get("at") or config.get("llm_schedule", {}).get("default", []),
            "starting_balance": cfg.get("starting_balance", 5000),
            "avatar": avatar_for(cfg, i), "mood": mood(cfg, book),
            "stats": _stats(db_path, name, book, trades),
            "book": book, "runs": runs, "journal": entries, "rejections": rejected,
        })
    all_trades.sort(key=lambda t: t["at"], reverse=True)
    return {"agents": agents, "trades": all_trades[:300]}
