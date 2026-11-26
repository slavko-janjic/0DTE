"""Feeds runner: collects Reddit posts, news headlines and SEC filings into
the shared database for the LLM agents' digests. Runs as its own scheduled
task (0DTE-Feeds) beside the worker, so a slow or failing outside site can
never delay the trading loop.

    python feeds.py            # loop forever (the scheduled task)
    python feeds.py --once     # run every feed once now and exit

Each feed has its own interval in config `feeds.intervals_minutes`; a feed is
due when that long has passed since its last run (stored in feed_status, so a
restart doesn't re-hit every site at once). Secrets (Reddit API keys) come
from the environment or a secrets.env file beside the database - never from
the repo.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import single_instance
from config import DatabaseNotMigrated, check_database_location, database_path, load_settings
from data import filings, news
from sentiment import reddit
from storage import db as storage
from storage import feeds as feed_store

LOG_PATH = Path(__file__).resolve().parent / "feeds.log"
LOCK_PORT = 47633                 # the worker holds 47632
TICK_SECONDS = 60
SYMBOLS_MAX_AGE_DAYS = 7
DEFAULT_INTERVALS = {"reddit": 30, "news": 15, "filings": 360}

log = logging.getLogger("feeds")


def load_secrets(db_path: str) -> None:
    """KEY=value lines from secrets.env beside the database into the
    environment, without overriding anything already set."""
    folder = Path(db_path).parent
    # Notepad likes to add .txt; utf-8-sig drops the byte-order mark Windows
    # editors put at the start, which would otherwise glue onto the first key.
    path = next((p for p in (folder / "secrets.env", folder / "secrets.env.txt") if p.exists()), None)
    if path is None:
        log.info("no secrets.env in %s", folder)
        return
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        loaded.append(key.strip())
    log.info("secrets loaded from %s: %s", path.name, ", ".join(loaded) or "none")


def due(status: dict, feed: str, minutes: float, now: datetime) -> bool:
    last = (status.get(feed) or {}).get("last_run")
    return last is None or now - datetime.fromisoformat(last) >= timedelta(minutes=minutes)


def refresh_symbols_if_stale(config: dict, db_path: str) -> None:
    updated = feed_store.symbols_updated_at(db_path)
    if updated and datetime.now(timezone.utc) - datetime.fromisoformat(updated) < timedelta(days=SYMBOLS_MAX_AGE_DAYS):
        return
    try:
        n = feed_store.replace_symbols(db_path, filings.fetch_symbols(config))
        feed_store.record_feed_run(db_path, "symbols", n)
        log.info("symbols: %d tickers loaded", n)
    except Exception as exc:  # noqa: BLE001
        feed_store.record_feed_run(db_path, "symbols", None, error=str(exc)[:500])
        log.warning("symbols: %s", exc)


def run_feed(name: str, config: dict, db_path: str) -> None:
    universe = feed_store.symbol_universe(db_path)
    errors: list[str] = []
    try:
        if name == "reddit":
            new = feed_store.upsert_reddit_posts(db_path, reddit.collect(config, universe))
        elif name == "news":
            items, errors = news.collect(config, universe)
            new = feed_store.insert_news_items(db_path, items)
        elif name == "filings":
            items, errors = filings.collect(config, lambda t: feed_store.cik_for(db_path, t))
            new = feed_store.insert_filings(db_path, items)
        else:
            raise ValueError(f"unknown feed {name}")
    except Exception as exc:  # noqa: BLE001 - one feed failing never stops the others
        feed_store.record_feed_run(db_path, name, None, error=str(exc)[:500])
        log.warning("%s: failed: %s", name, exc)
        return
    error = "; ".join(errors)[:500] or None
    feed_store.record_feed_run(db_path, name, new, error=error)
    log.info("%s: %d new%s", name, new, f" ({len(errors)} feed errors)" if errors else "")


def run_due(config: dict, db_path: str, force: bool = False) -> None:
    cfg = config.get("feeds", {})
    if not cfg.get("enabled", True):
        return
    refresh_symbols_if_stale(config, db_path)
    intervals = {**DEFAULT_INTERVALS, **cfg.get("intervals_minutes", {})}
    status = feed_store.feed_status(db_path)
    now = datetime.now(timezone.utc)
    for name, minutes in intervals.items():
        if minutes and (force or due(status, name, minutes, now)):
            run_feed(name, config, db_path)


def _setup_logging(to_file: bool) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if to_file:
        handlers.append(RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2,
                                            encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run every feed once and exit")
    args = parser.parse_args()
    _setup_logging(to_file=not args.once)

    config = load_settings()
    db_path = database_path(config)
    try:
        check_database_location(db_path)
    except DatabaseNotMigrated as exc:
        log.error("refusing to start: %s", exc)
        sys.exit(2)
    storage.init_db(db_path)
    load_secrets(db_path)
    log.info("reddit source: %s", "API" if reddit.has_api_credentials() else "RSS fallback")

    if args.once:
        run_due(config, db_path, force=True)
        return
    try:
        _lock = single_instance.acquire(LOCK_PORT)  # noqa: F841 - held for process lifetime
    except single_instance.AlreadyRunning as exc:
        log.info("another feeds runner is already running - exiting (%s)", exc)
        sys.exit(0)
    log.info("feeds runner started (pid %d)", os.getpid())
    while True:
        try:
            config = load_settings()    # picks up interval / watchlist edits without a restart
            run_due(config, db_path)
        except Exception:  # noqa: BLE001
            log.exception("feeds cycle failed")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
