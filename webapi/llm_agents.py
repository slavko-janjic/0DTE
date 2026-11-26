"""Payload for the Agents page: each LLM agent's book, latest wake-ups,
journal and rejected orders. Reads stored marks only (no live quotes), so the
page is instant; the agents re-mark their books every wake-up."""
from __future__ import annotations

import json

from broker import service
from storage import agent_journal as journal
from storage import agent_ledger as ledger


def _round(x, d=2):
    return None if x is None else round(x, d)


def agents_payload(db_path: str, config: dict) -> dict:
    out = []
    for cfg in config.get("llm_agents") or []:
        name = cfg.get("name")
        if not name:
            continue
        book = service.portfolio(db_path, config, name, remark=False) if ledger.get_account(db_path, name) else None
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
        out.append({
            "name": name, "label": cfg.get("label", name), "about": cfg.get("about"),
            "mode": cfg.get("mode", "dry_run"), "model": cfg.get("model", "sonnet"),
            "schedule": cfg.get("at") or config.get("llm_schedule", {}).get("default", []),
            "starting_balance": cfg.get("starting_balance", 5000),
            "book": book, "runs": runs, "journal": entries, "rejections": rejected,
        })
    return {"agents": out}
