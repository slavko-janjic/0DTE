"""Background polling loop: fetches data -> computes the composite signal ->
evaluates open positions for exit conditions -> persists everything to SQLite.

Run continuously during market hours:
    python worker.py

Run a single poll cycle immediately (for testing, ignores market hours):
    python worker.py --once
"""
import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics import accuracy
from config import load_settings
from data import kalshi, market_data, news
from paper_trading.engine import (
    AUTO_CLOSE_REASONS, buy as buy_position, calculate_contracts, close as close_position,
    evaluate_exit, should_auto_enter,
)
from paper_trading.models import Position
from sentiment.aggregate import get_sentiment
from signals import day_setup as day_setup_mod
from signals import indicators
from signals.composite import build_recommendation, compute_signal
from storage import db as storage


_HALF_DAY_CLOSE = "13:00"


def _close_time_for(config: dict, now: datetime) -> str:
    """Regular close, or 13:00 ET on NYSE early-close days."""
    if now.date().isoformat() in config.get("market_half_days", []):
        return _HALF_DAY_CLOSE
    return config["market_hours"].get("close", "16:00")


def minutes_to_market_close(config: dict, now: datetime | None = None) -> float:
    market_hours = config["market_hours"]
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    now = now if now is not None else datetime.now(tz)
    close_hour, close_minute = (int(p) for p in _close_time_for(config, now).split(":"))
    close_dt = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return (close_dt - now).total_seconds() / 60.0


def next_trading_day(config: dict, from_date: date) -> date:
    """The first weekday strictly after from_date that isn't a market holiday."""
    candidate = from_date + timedelta(days=1)
    holidays = set(config.get("market_holidays", []))
    while candidate.weekday() >= 5 or candidate.isoformat() in holidays:
        candidate += timedelta(days=1)
    return candidate


def minutes_since_market_open(config: dict, now: datetime | None = None) -> float:
    market_hours = config["market_hours"]
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    now = now if now is not None else datetime.now(tz)
    open_hour, open_minute = (int(p) for p in market_hours.get("open", "09:30").split(":"))
    open_dt = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    return (now - open_dt).total_seconds() / 60.0


def is_market_open(config: dict, now: datetime | None = None) -> bool:
    market_hours = config["market_hours"]
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    now = now if now is not None else datetime.now(tz)
    if now.weekday() >= 5:
        return False
    if now.date().isoformat() in config.get("market_holidays", []):
        return False
    open_hour, open_minute = (int(p) for p in market_hours.get("open", "09:30").split(":"))
    close_hour, close_minute = (int(p) for p in _close_time_for(config, now).split(":"))
    open_dt = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    close_dt = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return open_dt <= now <= close_dt


def _in_premarket_window(config: dict, now: datetime | None = None) -> bool:
    """True on a trading day when now sits in [open - window_minutes, open) -
    the slot where the pre-market day-setup pass runs. Weekends and holidays are
    excluded; the window length is configurable."""
    market_hours = config["market_hours"]
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    now = now if now is not None else datetime.now(tz)
    if now.weekday() >= 5:
        return False
    if now.date().isoformat() in config.get("market_holidays", []):
        return False
    window = config.get("premarket", {}).get("window_minutes", 90)
    open_hour, open_minute = (int(p) for p in market_hours.get("open", "09:30").split(":"))
    open_dt = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    return (open_dt - timedelta(minutes=window)) <= now < open_dt


def compute_subscores(
    ticker: str, config: dict, trump_headlines: list[str] | None = None,
    db_path: str | None = None,
) -> tuple[dict, market_data.OptionChainSnapshot | None, float | None]:
    bars = market_data.get_intraday_bars(ticker)
    technicals = None
    if bars is not None:
        closes = bars["Close"].tolist()
        volumes = bars["Volume"].tolist()
        highs = bars["High"].tolist() if "High" in bars else None
        lows = bars["Low"].tolist() if "Low" in bars else None
        technicals = indicators.compute_technicals_score(closes, volumes, highs, lows)

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

    prediction_markets = None
    series_map = kalshi.TICKER_SERIES_MAP.get(ticker)
    if series_map:
        kalshi_series, index_ticker = series_map
        ladder = kalshi.get_strike_ladder(kalshi_series)
        index_spot = market_data.get_current_price(index_ticker)
        if ladder is not None and index_spot is not None:
            prediction_markets = indicators.prediction_market_score(ladder, index_spot)

    volatility_regime = None
    vix_data = market_data.get_vix_term_structure()
    if vix_data is not None:
        volatility_regime = indicators.compute_volatility_regime_score(
            vix_data["vix9d"], vix_data["vix"], vix_data["vvix"]
        )

    trump_news = indicators.trump_headline_score(trump_headlines)

    subscores = {
        "technicals": technicals,
        "greeks_iv": greeks_iv,
        "order_flow": order_flow,
        "sentiment": sentiment_result.score,
        "prediction_markets": prediction_markets,
        "volatility_regime": volatility_regime,
        "trump_news": trump_news,
    }
    # manual config inversions plus any auto-calibration-added per-ticker ones
    if db_path is not None:
        inversions = storage.effective_inversions(db_path, config, ticker)
    else:
        inversions = config.get("invert_categories", [])
    subscores = apply_inversions(subscores, inversions)
    spot = chain.spot if chain is not None else market_data.get_current_price(ticker)
    return subscores, chain, spot


def apply_inversions(subscores: dict, invert_categories: list[str]) -> dict:
    """Flips the sign of categories the user has marked as contrarian in
    config/settings.yaml. Stored flipped, so downstream accuracy grading
    measures each signal as it was actually used in the composite."""
    return {
        cat: (-score if cat in invert_categories and score is not None else score)
        for cat, score in subscores.items()
    }


def poll_ticker(
    ticker: str, config: dict, db_path: str, trump_headlines: list[str] | None = None,
) -> tuple[object, market_data.OptionChainSnapshot | None] | None:
    """Polls one ticker and returns (signal, chain) so run_once can pick the
    strongest auto-entry candidate across ALL tickers, not the first in list
    order. Returns None when no data was available this cycle."""
    subscores, chain, spot = compute_subscores(ticker, config, trump_headlines, db_path)
    # read fresh each cycle so a dashboard-applied override takes effect without a restart
    weights = storage.effective_weights(db_path, config, ticker)
    signal = compute_signal(ticker, subscores, weights, config["confidence_floor_pct"])
    if signal is None:
        print(f"[{ticker}] no data available this cycle, skipping")
        return None

    # Calibrated confidence: raw stays in the `confidence` column (all grading
    # and future calibration key off raw, avoiding a feedback loop); the
    # calibrated value is what gets displayed and what autopilot decides on.
    raw_confidence = signal.confidence_pct
    bands = storage.get_confidence_bands(db_path, ticker)
    calibrated = accuracy.calibrated_confidence(
        raw_confidence, bands, config.get("calibration", {}).get("band_min_count", 5),
    )
    if calibrated != raw_confidence:
        signal.confidence_pct = calibrated
        signal.recommendation = build_recommendation(
            ticker, signal.direction, calibrated, config["confidence_floor_pct"],
        )

    # dealer-gamma regime (rangebound vs trending) from the chain we already
    # fetched - a tactic gate + display hint, not a directional subscore
    gamma_score, gamma_regime = _gamma_from_chain(chain, config)

    storage.insert_signal_snapshot(
        db_path, ticker, signal.direction, raw_confidence,
        signal.composite_score, signal.recommendation, signal.subscores_used,
        spot_price=spot,
        calibrated_confidence=calibrated if calibrated != raw_confidence else None,
        gamma_score=gamma_score, gamma_regime=gamma_regime,
    )
    print(f"[{ticker}] {signal.recommendation} (score={signal.composite_score:.2f})")

    check_open_positions(ticker, config, db_path, chain, signal.composite_score)
    return signal, chain


def _gamma_from_chain(chain: market_data.OptionChainSnapshot | None,
                      config: dict) -> tuple[float | None, str | None]:
    """Normalized dealer-gamma score (-1..1) and its regime label from an
    enriched chain (needs the 'gamma' + 'openInterest' columns). (None, None)
    when the chain or those columns are missing."""
    if chain is None:
        return None, None

    def _col(df, name):
        cols = getattr(df, "columns", None)
        if cols is None or name not in cols or df.empty:
            return []
        return df[name].tolist()

    score = indicators.gamma_exposure_score(
        _col(chain.calls, "gamma"), _col(chain.calls, "openInterest"),
        _col(chain.puts, "gamma"), _col(chain.puts, "openInterest"),
    )
    deadband = config.get("gamma", {}).get("deadband", 0.15)
    return score, indicators.gamma_regime(score, deadband)


def maybe_auto_enter_best(candidates: list[tuple[str, object, market_data.OptionChainSnapshot]],
                          config: dict, db_path: str) -> None:
    """Auto-pilot entry: considers this cycle's candidates strongest-first and
    opens at most ONE paper position - the top candidate whose guard rails all
    pass - tagging it 'auto' with the configured stop/target attached.
    Manual trading is unaffected - this only ADDS worker-side entries."""
    if not candidates:
        return
    if not storage.get_autopilot_enabled(db_path) or not is_market_open(config):
        return

    autopilot_cfg = config.get("autopilot", {})
    tz_name = config["market_hours"].get("timezone", "America/New_York")
    minutes_since_open = minutes_since_market_open(config)
    minutes_to_close = minutes_to_market_close(config)
    now = datetime.now(ZoneInfo(tz_name))
    # today's scheduled high-impact catalysts (from each ticker's stored setup)
    # gate the entry; setups share the same market_catalysts list per day
    catalysts_today = day_setup_mod.catalysts_for_date(
        config.get("market_catalysts", []), now.date(), tz_name)
    minutes_to_catalyst = day_setup_mod.minutes_to_next_catalyst(catalysts_today, now, tz_name)

    for ticker, signal, chain in sorted(
        candidates, key=lambda c: c[1].confidence_pct, reverse=True,
    ):
        if chain is None:
            continue
        _, gamma_regime = _gamma_from_chain(chain, config)
        # guard rails re-read per candidate: an entry above changes open_rows
        option_type = should_auto_enter(
            ticker=ticker,
            direction=signal.direction,
            confidence_pct=signal.confidence_pct,
            minutes_since_open=minutes_since_open,
            minutes_to_close=minutes_to_close,
            open_rows=storage.get_open_positions(db_path),
            closed_rows=storage.get_closed_positions(db_path),
            autopilot_cfg=autopilot_cfg,
            starting_balance=config["account"]["starting_balance"],
            now=now,
            tz_name=tz_name,
            minutes_to_catalyst=minutes_to_catalyst,
            gamma_regime=gamma_regime,
        )
        if option_type is None:
            continue

        df = chain.calls if option_type == "call" else chain.puts
        contract = market_data.find_atm_contract(df, chain.spot)
        if contract is None:
            continue
        entry_price = market_data.contract_entry_price(contract)  # honest fill: ask-side
        if entry_price is None:
            continue
        balance = storage.get_balance(db_path)
        contracts = calculate_contracts(balance, autopilot_cfg.get("risk_per_trade_pct", 5), entry_price)
        if contracts <= 0:
            continue

        position_id = buy_position(
            db_path, ticker, option_type, float(contract["strike"]),
            chain.expiration, entry_price, contracts, signal.composite_score,
            opened_by="auto",
        )
        if position_id:
            storage.set_position_exit_targets(
                db_path, position_id,
                autopilot_cfg.get("profit_target_pct", 50),
                autopilot_cfg.get("stop_loss_pct", -35),
            )
            print(f"[{ticker}] AUTO-OPENED position {position_id}: {contracts}x {option_type} "
                  f"{contract['strike']:g} @ ${entry_price:.2f} "
                  f"(confidence {signal.confidence_pct:.0f}%)")
            return  # one entry per cycle - the strongest qualifying candidate


def check_open_positions(ticker: str, config: dict, db_path: str,
                          chain: market_data.OptionChainSnapshot | None,
                          composite_score: float) -> None:
    minutes_to_close = minutes_to_market_close(config)
    catalyst_imminent = _catalyst_imminent(config)
    for row in storage.get_open_positions(db_path):
        if row["ticker"] != ticker:
            continue
        position = Position.from_row(row)
        current_price = market_data.find_contract_price(chain, position.option_type, position.strike)
        if current_price is None:
            continue
        storage.update_position_price(db_path, position.id, current_price)
        reason = evaluate_exit(position, current_price, composite_score,
                                minutes_to_close, config["exit_rules"])
        # per-trade stop/target always executes; time_cutoff executes for
        # auto-opened positions so a day-session ends flat (0DTE would expire
        # worthless) - manual positions keep suggestion-only behavior
        force_close = (reason in AUTO_CLOSE_REASONS
                       or (reason == "time_cutoff" and position.opened_by == "auto"))
        # imminent high-impact catalyst: don't hold an AUTO position into it
        # (force-close, overriding a weaker suggestion); manual gets a suggestion
        if not force_close and catalyst_imminent:
            if position.opened_by == "auto":
                reason, force_close = "catalyst", True
            elif not reason:
                reason = "catalyst"
        if force_close:
            pnl = close_position(db_path, position.id, current_price, reason)
            if pnl is not None:  # None = the dashboard already closed it this instant
                print(f"[{ticker}] AUTO-CLOSED position {position.id}: {reason} "
                      f"@ ${current_price:.2f} (P&L ${pnl:+,.2f})")
        elif reason:
            # time-cutoff (manual) / signal-reversal / catalyst remain suggestion-only
            storage.flag_suggested_exit(db_path, position.id, reason)
            print(f"[{ticker}] SUGGESTED EXIT for position {position.id}: {reason}")


def _catalyst_imminent(config: dict, now: datetime | None = None) -> bool:
    """True when a scheduled high-impact catalyst is within
    autopilot.close_before_catalyst_minutes from now (market tz). Drives the
    'don't hold into a catalyst' exit. Off when the knob is 0/unset."""
    window = config.get("autopilot", {}).get("close_before_catalyst_minutes", 0)
    if not window:
        return False
    tz_name = config["market_hours"].get("timezone", "America/New_York")
    now = now if now is not None else datetime.now(ZoneInfo(tz_name))
    catalysts_today = day_setup_mod.catalysts_for_date(
        config.get("market_catalysts", []), now.date(), tz_name)
    minutes = day_setup_mod.minutes_to_next_catalyst(catalysts_today, now, tz_name)
    return minutes is not None and 0 <= minutes <= window


def run_once(config: dict, db_path: str) -> None:
    # Fetched once per cycle, not per ticker - it's market-wide, and GDELT's free
    # tier expects light request pacing.
    trump_headlines = news.get_trump_market_headlines(
        timespan_hours=config.get("trump_news_lookback_hours", 6)
    )
    candidates = []
    for ticker in config["tickers"]:
        result = poll_ticker(ticker, config, db_path, trump_headlines)
        if result is not None:
            signal, chain = result
            candidates.append((ticker, signal, chain))
    maybe_auto_enter_best(candidates, config, db_path)


def run_premarket_setup(config: dict, db_path: str) -> None:
    """Once per trading day before the open: per ticker, assemble the overnight
    gap, prior-day/overnight levels, today's catalysts, and an opening bias, and
    store it (day_setups). Idempotent - skips a ticker whose setup for today
    already exists. Per-ticker try/except so one bad fetch can't abort the pass;
    every fetch degrades to None (a partial setup is still useful)."""
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    today = datetime.now(tz).date()
    proxies = config.get("premarket_proxies", {})
    scale = config.get("premarket", {}).get("gap_bias_scale_pct", 0.5)
    catalysts_today = day_setup_mod.catalysts_for_date(
        config.get("market_catalysts", []), today,
        config["market_hours"].get("timezone", "America/New_York"))

    for ticker in config["tickers"]:
        if storage.get_day_setup(db_path, ticker, today.isoformat()) is not None:
            continue
        try:
            proxy = proxies.get(ticker)
            premarket_quote = market_data.get_premarket_quote(ticker, proxy)
            prior_high = prior_low = prior_close = None
            daily = market_data.get_daily_bars(ticker, period="5d")
            if daily is not None and not daily.empty:
                prior = daily.iloc[-1]  # last completed daily bar (yesterday)
                prior_high, prior_low, prior_close = (
                    float(prior["High"]), float(prior["Low"]), float(prior["Close"]))
            overnight = market_data.get_overnight_range(ticker, proxy)
            overnight_high, overnight_low = overnight if overnight else (None, None)

            gap = day_setup_mod.gap_pct(premarket_quote, prior_close)
            # single names: fold an earnings-today flag into the catalyst list
            ticker_catalysts = list(catalysts_today)
            if not proxy:
                earnings = market_data.get_next_earnings_date(ticker)
                if earnings == today.isoformat():
                    ticker_catalysts.append(
                        {"time": None, "label": f"{ticker} earnings", "impact": "high"})

            setup = day_setup_mod.build_day_setup(
                ticker, gap, prior_high, prior_low, prior_close,
                overnight_high, overnight_low, ticker_catalysts, scale_pct=scale,
            )
            storage.set_day_setup(db_path, ticker, today.isoformat(), setup)
            gap_txt = f"{gap:+.2f}%" if gap is not None else "n/a"
            print(f"[{ticker}] day setup: gap {gap_txt}, "
                  f"prior close {prior_close if prior_close is not None else 'n/a'}, "
                  f"{len(ticker_catalysts)} catalyst(s)")
        except Exception as exc:
            print(f"[{ticker}] pre-market setup failed: {exc}")


def run_daily_calibration(config: dict, db_path: str) -> None:
    """Once per day after market close: per ticker, nudge weights toward the
    accuracy-based suggestion, add/remove signal inversions, and refresh the
    confidence map. Decisions come from accuracy.plan_calibration (pure); this
    function does the I/O and audit logging. Idempotent via last_run_date."""
    if not storage.get_calibration_enabled(db_path):
        return
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    today = datetime.now(tz).date()
    if storage.get_last_calibration_date(db_path) == today.isoformat():
        return

    cal = config.get("calibration", {})
    cal_cfg = {
        "learning_rate": cal.get("learning_rate", 0.25),
        "inversion_cooldown_days": cal.get("inversion_cooldown_days", 5),
        "confidence_min_graded": cal.get("confidence_min_graded", 20),
        "weight_min_graded": config.get("weight_suggestion_min_graded", 10),
        "inversion_min_graded": config.get("weight_suggestion_min_graded", 10),
        "inversion_max_accuracy_pct": 40.0,
        "horizon_minutes": config.get("accuracy_horizon_minutes", 30),
    }
    all_events = storage.get_calibration_events(db_path, limit=500)

    for ticker in config["tickers"]:
        try:
            history = accuracy.history_snapshots(storage.get_signal_history(db_path, ticker))
            current_weights = storage.effective_weights(db_path, config, ticker)
            recent_events = [
                {"kind": row["kind"], "detail": json.loads(row["detail_json"]),
                 "created_at": row["created_at"]}
                for row in all_events if row["ticker"] == ticker
            ]
            actions = accuracy.plan_calibration(
                ticker, history, current_weights,
                storage.effective_inversions(db_path, config, ticker),
                recent_events, cal_cfg, today,
            )
            for action in actions:
                if action["kind"] == "weights":
                    storage.set_weight_overrides(db_path, ticker, action["new"])
                    storage.log_calibration_event(db_path, ticker, "weights_nudged",
                                                  {"old": action["old"], "new": action["new"]})
                elif action["kind"] == "invert":
                    storage.add_inversion(db_path, ticker, action["category"])
                    storage.log_calibration_event(db_path, ticker, "inversion_added",
                                                  {"category": action["category"],
                                                   "accuracy_pct": action["accuracy_pct"]})
                elif action["kind"] == "uninvert":
                    # only DB-managed (auto-added) inversions are removable;
                    # manual config ones stay the user's call
                    if action["category"] in storage.get_inversions(db_path, ticker):
                        storage.remove_inversion(db_path, ticker, action["category"])
                        storage.log_calibration_event(db_path, ticker, "inversion_removed",
                                                      {"category": action["category"],
                                                       "accuracy_pct": action["accuracy_pct"]})
                elif action["kind"] == "confidence_map":
                    storage.set_confidence_bands(db_path, ticker, action["bands"])
                    storage.log_calibration_event(db_path, ticker, "confidence_map_updated",
                                                  {"bands": action["bands"]})
            if actions:
                print(f"[{ticker}] calibration: {len(actions)} adjustment(s) applied")
        except Exception as exc:
            print(f"[{ticker}] calibration failed: {exc}")

    storage.set_last_calibration_date(db_path, today.isoformat())


def maybe_disarm_day_session(config: dict, db_path: str) -> None:
    """When a day-session's armed date is in the past (market tz), switch
    auto-pilot back off and log a one-line summary of that day's auto trades."""
    mode, armed_date = storage.get_autopilot_state(db_path)
    if mode != "day" or not armed_date:
        return
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    if datetime.now(tz).date().isoformat() <= armed_date:
        return  # still armed for today or a future day

    storage.set_autopilot_state(db_path, "off")
    auto_closed = [
        row for row in storage.get_closed_positions(db_path)
        if row["opened_by"] == "auto" and row["exit_time"]
        and datetime.fromisoformat(row["exit_time"]).astimezone(tz).date().isoformat() == armed_date
    ]
    total_pnl = sum(row["pnl"] or 0.0 for row in auto_closed)
    print(f"day session {armed_date} ended: {len(auto_closed)} auto trade(s), "
          f"realized P&L ${total_pnl:+,.2f} - auto-pilot disarmed")


def run_loop(config: dict, db_path: str) -> None:
    while True:
        if is_market_open(config):
            run_once(config, db_path)
        else:
            print("market closed, sleeping")
            try:
                maybe_disarm_day_session(config, db_path)
            except Exception as exc:
                print(f"day-session disarm check failed: {exc}")
            # daily self-calibration - idempotent via last_run_date
            try:
                run_daily_calibration(config, db_path)
            except Exception as exc:
                print(f"calibration pass failed: {exc}")
            # pre-market day-setup pass - only in the window before the open,
            # idempotent per ticker/day
            try:
                if _in_premarket_window(config):
                    run_premarket_setup(config, db_path)
            except Exception as exc:
                print(f"pre-market setup failed: {exc}")
            # daily off-hours backup - idempotent, skips if today's file exists
            try:
                storage.backup_db(db_path, Path(db_path).parent / "backups")
            except Exception as exc:
                print(f"backup failed: {exc}")
        # read fresh each cycle so the dashboard can change it without a restart
        time.sleep(storage.get_poll_interval_seconds(db_path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    args = parser.parse_args()

    config = load_settings()
    db_path = config["database"]["path"]
    storage.init_db(db_path)
    storage.ensure_account(db_path, config["account"]["starting_balance"])
    storage.ensure_worker_settings(db_path, config["poll_interval_minutes"] * 60)
    storage.ensure_autopilot(db_path)  # off by default until toggled from the dashboard
    storage.ensure_calibration(db_path)  # auto-calibration on by default (dashboard toggle)

    if args.once:
        run_once(config, db_path)
    else:
        run_loop(config, db_path)


if __name__ == "__main__":
    main()
