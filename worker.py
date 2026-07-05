"""Background polling loop: fetches data -> computes the composite signal ->
evaluates open positions for exit conditions -> persists everything to SQLite.

Run continuously during market hours:
    python worker.py

Run a single poll cycle immediately (for testing, ignores market hours):
    python worker.py --once
"""
import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import load_settings
from data import market_data
from paper_trading.engine import evaluate_exit
from paper_trading.models import Position
from sentiment.aggregate import get_sentiment
from signals import indicators
from signals.composite import compute_signal
from storage import db as storage


def minutes_to_market_close(market_hours: dict) -> float:
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    now = datetime.now(tz)
    close_hour, close_minute = (int(p) for p in market_hours.get("close", "16:00").split(":"))
    close_dt = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return (close_dt - now).total_seconds() / 60.0


def is_market_open(market_hours: dict) -> bool:
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    open_hour, open_minute = (int(p) for p in market_hours.get("open", "09:30").split(":"))
    close_hour, close_minute = (int(p) for p in market_hours.get("close", "16:00").split(":"))
    open_dt = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    close_dt = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return open_dt <= now <= close_dt


def compute_subscores(ticker: str, config: dict) -> tuple[dict, market_data.OptionChainSnapshot | None]:
    bars = market_data.get_intraday_bars(ticker)
    technicals = None
    if bars is not None:
        closes = bars["Close"].tolist()
        volumes = bars["Volume"].tolist()
        technicals = indicators.compute_technicals_score(closes, volumes)

    chain = market_data.get_option_chain(ticker)
    greeks_iv = order_flow = None
    if chain is not None:
        chain = market_data.enrich_with_greeks(chain)
        atm_call = market_data.find_atm_contract(chain.calls, chain.spot)
        atm_put = market_data.find_atm_contract(chain.puts, chain.spot)
        if atm_call is not None and atm_put is not None:
            greeks_iv = indicators.compute_greeks_iv_score(
                atm_call.get("impliedVolatility"), atm_put.get("impliedVolatility")
            )

        call_volume = chain.calls["volume"].fillna(0).sum() if not chain.calls.empty else 0
        put_volume = chain.puts["volume"].fillna(0).sum() if not chain.puts.empty else 0
        max_pain = indicators.compute_max_pain(
            chain.calls["strike"].tolist(),
            chain.calls["openInterest"].fillna(0).tolist(),
            chain.puts["openInterest"].fillna(0).tolist(),
        ) if not chain.calls.empty and not chain.puts.empty else None
        order_flow = indicators.compute_order_flow_score(call_volume, put_volume, max_pain, chain.spot)

    sentiment_result = get_sentiment(ticker, config)

    subscores = {
        "technicals": technicals,
        "greeks_iv": greeks_iv,
        "order_flow": order_flow,
        "sentiment": sentiment_result.score,
    }
    return subscores, chain


def poll_ticker(ticker: str, config: dict, db_path: str) -> None:
    subscores, chain = compute_subscores(ticker, config)
    signal = compute_signal(ticker, subscores, config["weights"], config["confidence_floor_pct"])
    if signal is None:
        print(f"[{ticker}] no data available this cycle, skipping")
        return

    storage.insert_signal_snapshot(
        db_path, ticker, signal.direction, signal.confidence_pct,
        signal.composite_score, signal.recommendation, signal.subscores_used,
    )
    print(f"[{ticker}] {signal.recommendation} (score={signal.composite_score:.2f})")

    check_open_positions(ticker, config, db_path, chain, signal.composite_score)


def check_open_positions(ticker: str, config: dict, db_path: str,
                          chain: market_data.OptionChainSnapshot | None,
                          composite_score: float) -> None:
    minutes_to_close = minutes_to_market_close(config["market_hours"])
    for row in storage.get_open_positions(db_path):
        if row["ticker"] != ticker:
            continue
        position = Position.from_row(row)
        current_price = _current_contract_price(chain, position)
        if current_price is None:
            continue
        storage.update_position_price(db_path, position.id, current_price)
        reason = evaluate_exit(position, current_price, composite_score,
                                minutes_to_close, config["exit_rules"])
        if reason:
            storage.flag_suggested_exit(db_path, position.id, reason)
            print(f"[{ticker}] SUGGESTED EXIT for position {position.id}: {reason}")


def _current_contract_price(chain: market_data.OptionChainSnapshot | None, position: Position) -> float | None:
    if chain is None:
        return None
    df = chain.calls if position.option_type == "call" else chain.puts
    match = df[df["strike"] == position.strike]
    if match.empty:
        return None
    return float(match.iloc[0]["lastPrice"])


def run_once(config: dict, db_path: str) -> None:
    for ticker in config["tickers"]:
        poll_ticker(ticker, config, db_path)


def run_loop(config: dict, db_path: str) -> None:
    poll_seconds = config["poll_interval_minutes"] * 60
    while True:
        if is_market_open(config["market_hours"]):
            run_once(config, db_path)
        else:
            print("market closed, sleeping")
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    args = parser.parse_args()

    config = load_settings()
    db_path = config["database"]["path"]
    storage.init_db(db_path)
    storage.ensure_account(db_path, config["account"]["starting_balance"])

    if args.once:
        run_once(config, db_path)
    else:
        run_loop(config, db_path)


if __name__ == "__main__":
    main()
