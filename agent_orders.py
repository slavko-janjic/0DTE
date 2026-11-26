"""Place agent orders by hand, through the same risk gate and ledger the LLM
agents will use - for testing the broker layer and for manual overrides.

    python agent_orders.py status [--agent NAME]
    python agent_orders.py buy  TSLA 5             [--agent NAME] [--limit 380]
    python agent_orders.py sell TSLA 5
    python agent_orders.py buy  QQQ 1 --call 600 --exp 2026-12-18 --limit 4.20
    python agent_orders.py sell QQQ 1 --put  590 --exp 2026-12-18 --limit 3.10
    python agent_orders.py settle                  # close expired options
    python agent_orders.py pause|resume [--agent NAME]

The default agent is `manual_test`, a $5,000 book that exists only for this.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from broker import service
from broker.models import OrderRequest
from config import database_path, load_settings
from feeds import load_secrets
from storage import agent_ledger as ledger
from storage import db as storage


def agent_config(config: dict, name: str) -> dict:
    for a in config.get("agents") or []:
        if a.get("name") == name:
            return a
    return {}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["buy", "sell", "status", "settle", "pause", "resume"])
    p.add_argument("symbol", nargs="?")
    p.add_argument("qty", nargs="?", type=int)
    p.add_argument("--agent", default="manual_test")
    p.add_argument("--call", type=float, metavar="STRIKE")
    p.add_argument("--put", type=float, metavar="STRIKE")
    p.add_argument("--exp", help="option expiration YYYY-MM-DD")
    p.add_argument("--limit", type=float)
    p.add_argument("--reason", default="manual order")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = load_settings()
    db_path = database_path(config)
    storage.init_db(db_path)
    load_secrets(db_path)

    if args.action == "settle":
        print(f"settled {service.settle_expired(db_path, config)} expired option position(s)")
        return 0
    if args.action in ("pause", "resume"):
        ledger.set_paused(db_path, args.agent, args.action == "pause", "paused by hand")
        print(f"{args.agent}: {args.action}d")
        return 0
    if args.action == "status":
        if ledger.get_account(db_path, args.agent) is None:
            print(f"{args.agent} has no account yet")
            return 0
        print(json.dumps(service.portfolio(db_path, config, args.agent), indent=2))
        return 0

    if not args.symbol or not args.qty:
        p.error("buy/sell need SYMBOL and QTY")
    is_option = args.call is not None or args.put is not None
    order = OrderRequest(
        agent=args.agent, symbol=args.symbol, side=args.action, qty=args.qty,
        asset="option" if is_option else "stock",
        option_type=("call" if args.call is not None else "put") if is_option else None,
        strike=args.call if args.call is not None else args.put,
        expiration=args.exp, limit_price=args.limit, reason=args.reason)
    result = service.submit(db_path, config, order, agent_config(config, args.agent))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "filled" else 1


if __name__ == "__main__":
    sys.exit(main())
