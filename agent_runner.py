"""Runs the LLM agents (config `llm_agents`) at their scheduled times, as its
own scheduled task (0DTE-Agents) beside the worker and the feeds runner.

    python agent_runner.py                       # loop forever (the scheduled task)
    python agent_runner.py --run momentum        # one wake-up now, in the agent's mode
    python agent_runner.py --run momentum --dry-run   # one wake-up, never fills

Each agent has a `mode`: off (never runs), dry_run (decides and journals;
orders are checked against live quotes and the risk gate but never filled),
or live (paper fills). Wake-up times are exchange time on trading days; a
daily token cap per agent (`max_tokens_per_day`) skips further runs once hit.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import scheduling
import single_instance
from agents.runner import run_agent
from config import DatabaseNotMigrated, check_database_location, database_path, load_settings
from feeds import load_secrets
from storage import agent_journal as journal
from storage import db as storage

LOG_PATH = Path(__file__).resolve().parent / "agents.log"
LOCK_PORT = 47634
TICK_SECONDS = 60
DEFAULT_TIMES = ["09:45", "11:00", "12:30", "14:00", "15:30"]

log = logging.getLogger("agents")


def llm_agents(config: dict) -> list[dict]:
    return [a for a in config.get("llm_agents") or [] if a.get("name")]


def find_agent(config: dict, name: str) -> dict | None:
    return next((a for a in llm_agents(config) if a["name"] == name), None)


def over_token_cap(db_path: str, config: dict, agent: dict, now: datetime) -> bool:
    cap = agent.get("max_tokens_per_day")
    if not cap:
        return False
    tz = ZoneInfo(config.get("market_hours", {}).get("timezone", "America/New_York"))
    start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return journal.tokens_today(db_path, agent["name"], start.astimezone(timezone.utc).isoformat()) >= cap


def due_agents(db_path: str, config: dict, now: datetime) -> list[dict]:
    out = []
    for agent in llm_agents(config):
        if agent.get("mode", "dry_run") == "off":
            continue
        times = agent.get("at") or config.get("llm_schedule", {}).get("default", DEFAULT_TIMES)
        if scheduling.slot_due(times, config, journal.last_run_started(db_path, agent["name"]), now) is None:
            continue
        if over_token_cap(db_path, config, agent, now):
            log.info("%s: daily token cap reached, skipping", agent["name"])
            continue
        out.append(agent)
    return out


def _setup_logging(to_file: bool) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if to_file:
        handlers.append(RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", metavar="AGENT", help="one wake-up of this agent now, then exit")
    p.add_argument("--dry-run", action="store_true", help="with --run: never fill, whatever the config says")
    args = p.parse_args()
    _setup_logging(to_file=not args.run)
    config = load_settings()
    db_path = database_path(config)
    try:
        check_database_location(db_path)
    except DatabaseNotMigrated as exc:
        log.error("refusing to start: %s", exc)
        sys.exit(2)
    storage.init_db(db_path)
    load_secrets(db_path)

    if args.run:
        agent = find_agent(config, args.run)
        if agent is None:
            print(f"no llm_agents entry named {args.run!r}")
            sys.exit(1)
        mode = "dry_run" if args.dry_run or agent.get("mode") != "live" else "live"
        result = run_agent(db_path, config, agent, mode=mode)
        print(result)
        for e in reversed(journal.recent_entries(db_path, agent["name"], limit=10)):
            if e["run_id"] == result["run_id"]:
                print(f"\n[{e['action']} {e['ticker'] or ''}] {e['thesis']}"
                      + (f"\n  orders: {e['orders_json']}" if e["orders_json"] else ""))
        return

    try:
        _lock = single_instance.acquire(LOCK_PORT)  # noqa: F841
    except single_instance.AlreadyRunning as exc:
        log.info("another agent runner is already running - exiting (%s)", exc)
        sys.exit(0)
    log.info("agent runner started (pid %d): %s", os.getpid(),
             ", ".join(f"{a['name']}={a.get('mode', 'dry_run')}" for a in llm_agents(config)) or "no agents")
    while True:
        try:
            config = load_settings()
            for agent in due_agents(db_path, config, datetime.now(timezone.utc)):
                run_agent(db_path, config, agent)
        except Exception:  # noqa: BLE001
            log.exception("agent cycle failed")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
