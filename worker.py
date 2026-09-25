"""Background polling loop: fetches data -> computes the composite signal ->
evaluates open positions for exit conditions -> persists everything to SQLite.

Run continuously during market hours:
    python worker.py

Run a single poll cycle immediately (for testing, ignores market hours):
    python worker.py --once
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import single_instance

from analytics import accuracy
from config import load_settings
from data import market_data
from paper_trading.engine import (
    AUTO_CLOSE_REASONS, AUTO_FORCE_REASONS, buy as buy_position, calculate_contracts,
    close as close_position, evaluate_exit, explain_auto_decision, settlement_price,
    should_auto_enter,
)
from paper_trading.models import Position
from paper_trading.shadow import shadow_exit_score, shadow_position_from_row, should_shadow_enter
from signals import day_setup as day_setup_mod
from signals import indicators
from signals.composite import build_recommendation, compute_signal, direction_from_score
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


def _is_trading_day(config: dict, day: date) -> bool:
    return day.weekday() < 5 and day.isoformat() not in config.get("market_holidays", [])


def session_bounds(config: dict, day: date) -> tuple[datetime, datetime]:
    """(open, close) of the given day's regular session as aware datetimes in
    the market timezone - 13:00 close on early-close days."""
    market_hours = config["market_hours"]
    tz = ZoneInfo(market_hours.get("timezone", "America/New_York"))
    open_h, open_m = (int(p) for p in market_hours.get("open", "09:30").split(":"))
    close_hm = (_HALF_DAY_CLOSE if day.isoformat() in config.get("market_half_days", [])
                else market_hours.get("close", "16:00"))
    close_h, close_m = (int(p) for p in close_hm.split(":"))
    return (datetime(day.year, day.month, day.day, open_h, open_m, tzinfo=tz),
            datetime(day.year, day.month, day.day, close_h, close_m, tzinfo=tz))


def last_completed_session(config: dict, now: datetime | None = None) -> date:
    """The most recent trading day whose session has already closed - today
    after the bell, otherwise the trading day before. Weekends and holidays
    never produce a new one, which is what makes it a clean once-per-session key."""
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    now = now if now is not None else datetime.now(tz)
    day = now.astimezone(tz).date()
    if _is_trading_day(config, day) and now >= session_bounds(config, day)[1]:
        return day
    day -= timedelta(days=1)
    while not _is_trading_day(config, day):
        day -= timedelta(days=1)
    return day


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
    ticker: str, config: dict, db_path: str | None = None,
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
    order_flow = None
    if chain is not None:
        chain = market_data.enrich_with_greeks(chain)
        call_volume = chain.calls["volume"].fillna(0).sum() if not chain.calls.empty else 0
        put_volume = chain.puts["volume"].fillna(0).sum() if not chain.puts.empty else 0
        max_pain = indicators.compute_max_pain(
            chain.calls["strike"].tolist(),
            chain.calls["openInterest"].fillna(0).tolist(),
            chain.puts["openInterest"].fillna(0).tolist(),
        ) if not chain.calls.empty and not chain.puts.empty else None
        order_flow = indicators.compute_order_flow_score(call_volume, put_volume, max_pain, chain.spot)


    # Scored against its OWN recent median, not its level: contango is the
    # normal state, so scoring the level reported "bullish" permanently (never
    # once negative in 9,874 samples). Absent until enough history exists.
    volatility_regime = None
    vix_data = market_data.get_vix_term_structure()
    if vix_data is not None:
        baselines = storage.get_vix_baselines(db_path) if db_path else None
        volatility_regime = indicators.compute_volatility_regime_score(
            vix_data["vix9d"], vix_data["vix"], vix_data["vvix"], baselines,
        )

    subscores = {
        "technicals": technicals,
        "order_flow": order_flow,
        "volatility_regime": volatility_regime,
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
    ticker: str, config: dict, db_path: str,
) -> tuple[object, market_data.OptionChainSnapshot | None] | None:
    """Polls one ticker and returns (signal, chain) so run_once can pick the
    strongest auto-entry candidate across ALL tickers, not the first in list
    order. Returns None when no data was available this cycle."""
    subscores, chain, spot = compute_subscores(ticker, config, db_path)
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
    # how long this direction has held - the composite itself is memoryless, so
    # persistence is tracked here (O(1), off the previous snapshot)
    direction_streak = storage.next_direction_streak(db_path, ticker, signal.direction)

    storage.insert_signal_snapshot(
        db_path, ticker, signal.direction, raw_confidence,
        signal.composite_score, signal.recommendation, signal.subscores_used,
        spot_price=spot,
        calibrated_confidence=calibrated if calibrated != raw_confidence else None,
        gamma_score=gamma_score, gamma_regime=gamma_regime,
        direction_streak=direction_streak,
    )
    print(f"[{ticker}] {signal.recommendation} (score={signal.composite_score:.2f})")

    check_open_positions(ticker, config, db_path, chain, signal.composite_score)
    # record what transacting costs right now - the one quantity here that's a
    # known toll rather than a forecast. Observation only; never blocks the loop.
    try:
        record_quote_snapshot(ticker, config, db_path, chain)
    except Exception as exc:
        print(f"[{ticker}] quote snapshot failed: {exc}")
    # shadow lab is bookkeeping only - a bug there must never break the real loop
    try:
        process_shadow_strategies(ticker, config, db_path, chain, signal,
                                  gamma_regime, direction_streak)
    except Exception as exc:
        print(f"[{ticker}] shadow lab failed: {exc}")
    return signal, chain


def record_quote_snapshot(ticker: str, config: dict, db_path: str,
                          chain: market_data.OptionChainSnapshot | None) -> None:
    """Logs the ATM call's bid/ask so the intraday cost curve can be built later.
    The spread is the toll every round trip pays - unlike direction it's knowable
    in advance, and it follows a structural daily shape worth timing around.

    Market-hours only: outside them there are no live quotes (yfinance returns
    empty bid/ask), so a `--once` run after the bell would just seed the cost
    curve with junk."""
    if chain is None or not is_market_open(config):
        return
    contract = market_data.find_atm_contract(chain.calls, chain.spot)
    if contract is None:
        return
    bid, ask, mid, spread_pct = market_data.contract_quote(contract)
    storage.insert_quote_snapshot(
        db_path, ticker, "call", float(contract["strike"]), chain.spot,
        bid, ask, mid, spread_pct, minutes_since_market_open(config),
    )


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
    # Optional whitelist: auto-enter only these tickers (the worker still polls
    # and stores signals for ALL tickers - this only narrows what autopilot
    # trades). Empty/absent = every polled ticker is eligible.
    allowed = autopilot_cfg.get("tickers")
    if allowed:
        candidates = [c for c in candidates if c[0] in allowed]
        if not candidates:
            return
    tz_name = config["market_hours"].get("timezone", "America/New_York")
    minutes_since_open = minutes_since_market_open(config)
    minutes_to_close = minutes_to_market_close(config)
    now = datetime.now(ZoneInfo(tz_name))
    # today's scheduled high-impact catalysts (from each ticker's stored setup)
    # gate the entry; setups share the same market_catalysts list per day
    catalysts_today = day_setup_mod.catalysts_for_date(
        config.get("market_catalysts", []), now.date(), tz_name)
    minutes_to_catalyst = day_setup_mod.minutes_to_next_catalyst(catalysts_today, now, tz_name)

    # Dry-run the decision for EVERY candidate first, log a one-line intent
    # summary (the "what's it about to do" trail), then act on the top qualifier.
    # At most one entry per cycle, so a single pre-cycle rows snapshot is exact.
    open_rows = storage.get_open_positions(db_path)
    closed_rows = storage.get_closed_positions(db_path)
    intents = []
    for ticker, signal, chain in sorted(
        candidates, key=lambda c: c[1].confidence_pct, reverse=True,
    ):
        if chain is None:
            continue
        _, gamma_regime = _gamma_from_chain(chain, config)
        intent = explain_auto_decision(
            ticker=ticker,
            direction=signal.direction,
            confidence_pct=signal.confidence_pct,
            minutes_since_open=minutes_since_open,
            minutes_to_close=minutes_to_close,
            open_rows=open_rows,
            closed_rows=closed_rows,
            autopilot_cfg=autopilot_cfg,
            starting_balance=config["account"]["starting_balance"],
            now=now,
            tz_name=tz_name,
            minutes_to_catalyst=minutes_to_catalyst,
            gamma_regime=gamma_regime,
        )
        intents.append((intent, ticker, signal, chain))

    if intents:
        summary = " | ".join(
            f"{i.ticker} {i.lean or 'neutral'} {i.confidence_pct:.0f}% "
            + ("-> WOULD ENTER" if i.would_enter else f"({i.blocker})")
            for i, _, _, _ in intents
        )
        print(f"autopilot intent: {summary}")

    for intent, ticker, signal, chain in intents:
        if not intent.would_enter:
            continue
        option_type = intent.lean

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
        current_price = market_data.find_contract_price(
            chain, position.option_type, position.strike, position.expiration)
        if current_price is None:
            continue
        storage.update_position_price(db_path, position.id, current_price)
        reason = evaluate_exit(position, current_price, composite_score,
                                minutes_to_close, config["exit_rules"])
        # per-trade stop/target always executes; the global risk exits
        # (time_cutoff, trailing_stop, time_decay_stop) force-close AUTO
        # positions so a day-session is managed end to end - manual positions
        # keep suggestion-only behavior
        force_close = (reason in AUTO_CLOSE_REASONS
                       or (reason in AUTO_FORCE_REASONS and position.opened_by == "auto"))
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


def is_expired(config: dict, expiration: str, now: datetime | None = None) -> bool:
    """True once the expiration day's session has closed (market tz)."""
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    now = now if now is not None else datetime.now(tz)
    return now >= session_bounds(config, date.fromisoformat(expiration))[1]


def expiry_settlement_price(config: dict, db_path: str, ticker: str, option_type: str,
                            strike: float, expiration: str) -> float:
    """What an expired contract settles at: intrinsic value against the last
    spot the worker recorded in that day's session (a few minutes' grace past
    the bell for the final poll). 0.0 when no spot was recorded that day."""
    session_open, session_close = session_bounds(config, date.fromisoformat(expiration))
    utc = ZoneInfo("UTC")
    spot = storage.get_final_spot(
        db_path, ticker, session_open.astimezone(utc).isoformat(),
        (session_close + timedelta(minutes=5)).astimezone(utc).isoformat())
    return settlement_price(option_type, strike, spot)


def settle_expired_positions(config: dict, db_path: str, now: datetime | None = None) -> None:
    """Closes every real position whose expiration session has ended, at its
    settlement value. Without this an unmanaged position (a manual one past its
    suggested time cutoff, or anything open while the worker was down) stayed
    open forever - and got re-priced against the NEXT expiry's same strike."""
    for row in storage.get_open_positions(db_path):
        if not is_expired(config, row["expiration"], now):
            continue
        price = expiry_settlement_price(config, db_path, row["ticker"], row["option_type"],
                                        row["strike"], row["expiration"])
        pnl = close_position(db_path, row["id"], price, "expired")
        if pnl is not None:
            print(f"[{row['ticker']}] EXPIRED position {row['id']}: settled "
                  f"@ ${price:.2f} (P&L ${pnl:+,.2f})")


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


def process_shadow_strategies(ticker: str, config: dict, db_path: str,
                              chain: market_data.OptionChainSnapshot | None,
                              signal, gamma_regime: str | None,
                              direction_streak: int = 1) -> None:
    """Runs every configured shadow strategy against this ticker's fresh signal:
    manages exits on their open virtual positions (same evaluate_exit machinery
    and honest bid-side fills as the real book), then considers one entry per
    strategy (ask-side fill, full audit of the reasoning at entry). Positions
    whose expiration has passed settle at intrinsic value (0.00 when OTM or
    when no final spot was recorded) - an unmanaged 0DTE isn't sold at a
    quote, and the shadow book stays honest about that."""
    strategies = config.get("shadow_strategies", [])
    if not strategies:
        return

    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    today = datetime.now(tz).date()
    minutes_to_close = minutes_to_market_close(config)
    minutes_since_open = minutes_since_market_open(config)
    open_rows = storage.get_open_shadow_positions(db_path, ticker)
    open_by_strategy = {}
    for row in open_rows:
        open_by_strategy.setdefault(row["strategy"], []).append(row)

    for strategy in strategies:
        name = strategy.get("name")
        if not name:
            continue
        exit_cfg = strategy.get("exit", {})
        entry_cfg = strategy.get("entry", {})

        # --- exits on this strategy's open positions in this ticker ---------
        for row in open_by_strategy.get(name, []):
            if is_expired(config, row["expiration"]):
                price = expiry_settlement_price(config, db_path, ticker, row["option_type"],
                                                row["strike"], row["expiration"])
                storage.close_shadow_position(db_path, row["id"], price, "expired")
                print(f"[{ticker}] SHADOW {name}: position {row['id']} expired "
                      f"@ ${price:.2f}")
                continue
            current_price = market_data.find_contract_price(
                chain, row["option_type"], row["strike"], row["expiration"])
            if current_price is None:
                continue
            storage.update_shadow_price(db_path, row["id"], current_price)
            position = shadow_position_from_row(row, exit_cfg)
            position.max_price = max(position.max_price or current_price, current_price)
            reason = evaluate_exit(position, current_price,
                                   shadow_exit_score(entry_cfg, signal.composite_score),
                                   minutes_to_close, exit_cfg)
            if reason:
                pnl = storage.close_shadow_position(db_path, row["id"], current_price, reason)
                if pnl is not None:
                    print(f"[{ticker}] SHADOW {name}: closed {row['id']} {reason} "
                          f"@ ${current_price:.2f} (P&L ${pnl:+,.2f})")

        # --- one possible entry per strategy per cycle -----------------------
        if chain is None or not is_market_open(config):
            continue

        # a strategy may be pinned to specific tickers (exits above still run for
        # any position it already holds, in case the pinning changed)
        only_tickers = entry_cfg.get("only_tickers")
        if only_tickers and ticker not in only_tickers:
            continue

        # ...and may trade ONE subscore instead of the blended composite, to test
        # a single signal on its own (the composite is 7 signals averaged, which
        # can dilute a good one). Confidence mirrors how the composite derives
        # it: |score| * 100.
        source = entry_cfg.get("signal_source")
        if source:
            score = signal.subscores_used.get(source)
            if score is None:
                continue  # that signal had no data this cycle
            entry_direction = direction_from_score(score)
            entry_confidence = abs(score) * 100.0
        else:
            entry_direction, entry_confidence = signal.direction, signal.confidence_pct

        has_open = any(row["expiration"] >= today.isoformat()
                       for row in open_by_strategy.get(name, []))
        option_type = should_shadow_enter(
            entry_cfg,
            entry_direction, entry_confidence,
            minutes_since_open, minutes_to_close,
            gamma_regime, signal.subscores_used,
            has_open,
            storage.count_shadow_entries_today(db_path, name, ticker, today.isoformat()),
            direction_streak,
        )
        if option_type is None:
            continue
        df = chain.calls if option_type == "call" else chain.puts
        contract = market_data.find_atm_contract(df, chain.spot)
        if contract is None:
            continue
        entry_price = market_data.contract_entry_price(contract)
        if entry_price is None:
            continue
        entry_reason = {
            # what this strategy actually acted on (differs from the composite
            # when signal_source pins it to a single subscore)
            "signal_source": source or "composite",
            "direction": entry_direction,
            "confidence_pct": entry_confidence,
            "composite_score": signal.composite_score,
            "composite_confidence_pct": signal.confidence_pct,
            "gamma_regime": gamma_regime,
            "direction_streak": direction_streak,
            "minutes_since_open": round(minutes_since_open, 1),
            "subscores": signal.subscores_used,
        }
        sid = storage.open_shadow_position(
            db_path, name, ticker, option_type, float(contract["strike"]),
            chain.expiration, entry_price, entry_reason,
        )
        print(f"[{ticker}] SHADOW {name}: opened {sid} {option_type} "
              f"{contract['strike']:g} @ ${entry_price:.2f}")


def run_once(config: dict, db_path: str) -> None:
    # The VIX complex, market-wide, once per cycle - this is what lets
    # volatility_regime be scored against its own normal instead of a guess.
    try:
        vix = market_data.get_vix_term_structure()
        if vix is not None:
            storage.insert_vix_snapshot(db_path, vix["vix"], vix["vix9d"], vix["vvix"])
    except Exception as exc:
        print(f"vix snapshot failed: {exc}")

    try:
        settle_expired_positions(config, db_path)
    except Exception as exc:
        print(f"expiry settlement failed: {exc}")

    candidates = []
    for ticker in config["tickers"]:
        # one ticker's bad data (a None frame, a missing column) must not take
        # down the whole cycle - it used to crash the worker outright
        try:
            result = poll_ticker(ticker, config, db_path)
        except Exception:
            import traceback
            print(f"[{ticker}] poll failed, skipping this cycle:")
            traceback.print_exc(file=sys.stdout)
            continue
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
    scale = config.get("premarket", {}).get("gap_bias_scale_pct", 0.5)
    catalysts_today = day_setup_mod.catalysts_for_date(
        config.get("market_catalysts", []), today,
        config["market_hours"].get("timezone", "America/New_York"))

    for ticker in config["tickers"]:
        if storage.get_day_setup(db_path, ticker, today.isoformat()) is not None:
            continue
        try:
            # Everything comes from the TICKER's own data, all in its own price
            # units. The old code sourced the overnight quote and range from an
            # index-futures proxy (QQQ->NQ=F etc.) but compared/stored them
            # against the ETF's own close: gap came out at +4023% (28,727 vs
            # 696.7) and the overnight range was NASDAQ-100 index levels (~28,879)
            # drawn on a ~696 QQQ chart, squashing the price line flat.
            premarket_quote = market_data.get_premarket_quote(ticker)
            prior_high = prior_low = prior_close = None
            daily = market_data.get_daily_bars(ticker, period="5d")
            if daily is not None and not daily.empty:
                prior = daily.iloc[-1]  # last completed daily bar (yesterday)
                prior_high, prior_low, prior_close = (
                    float(prior["High"]), float(prior["Low"]), float(prior["Close"]))
            overnight = market_data.get_overnight_range(ticker)
            overnight_high, overnight_low = overnight if overnight else (None, None)

            gap = day_setup_mod.gap_pct(premarket_quote, prior_close)
            # fold an earnings-today flag into the catalyst list (ETFs have no
            # earnings, so get_next_earnings_date returns None for them)
            ticker_catalysts = list(catalysts_today)
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
    """Once per trading SESSION, after its close: per ticker, nudge weights
    toward the accuracy-based suggestion, add/remove signal inversions, and
    refresh the confidence map. Decisions come from accuracy.plan_calibration
    (pure); this function does the I/O and audit logging.

    Idempotent via last_run_date, which records the session calibrated - not
    the calendar day. Keyed by calendar day it re-ran every weekend day and
    holiday on unchanged data, compounding the same weight nudge 3x a weekend."""
    if not storage.get_calibration_enabled(db_path):
        return
    tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    today = datetime.now(tz).date()
    session = last_completed_session(config)
    if storage.get_last_calibration_date(db_path) == session.isoformat():
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

    storage.set_last_calibration_date(db_path, session.isoformat())


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
    pid = os.getpid()
    while True:
        if is_market_open(config):
            run_once(config, db_path)
            note = "polled"
        else:
            print("market closed, sleeping")
            note = "market closed"
            try:
                maybe_disarm_day_session(config, db_path)
            except Exception as exc:
                print(f"day-session disarm check failed: {exc}")
            # settle anything left open past its expiration's closing bell
            try:
                settle_expired_positions(config, db_path)
            except Exception as exc:
                print(f"expiry settlement failed: {exc}")
            # self-calibration - once per completed session, via last_run_date
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
        # heartbeat every cycle (open or closed) so the dashboard can tell a
        # live worker from a dead one - a silent outage cost ~2 weeks of data
        try:
            storage.record_heartbeat(db_path, pid, note)
        except Exception as exc:
            print(f"heartbeat failed: {exc}")
        # read fresh each cycle so the dashboard can change it without a restart
        time.sleep(storage.get_poll_interval_seconds(db_path))


def _setup_file_logging() -> None:
    """In scheduled / non-interactive runs, tee stdout+stderr to worker.log
    with simple size rotation - this replaces the old run_worker.bat wrapper.
    Running the task as python.exe directly (no cmd.exe parent) means Task
    Scheduler tracks and terminates the worker process itself, so stopping the
    task can no longer leave an orphaned python child holding the lock.
    Interactive runs keep printing to the console."""
    stdout = sys.stdout
    if stdout is not None and stdout.isatty():
        return
    log = Path(__file__).resolve().parent / "worker.log"
    try:
        if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
            os.replace(log, log.parent / (log.name + ".old"))
    except OSError:
        pass
    handle = open(log, "a", buffering=1, encoding="utf-8")
    sys.stdout = handle
    sys.stderr = handle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    args = parser.parse_args()
    if not args.once:
        _setup_file_logging()

    config = load_settings()
    db_path = config["database"]["path"]
    storage.init_db(db_path)
    storage.ensure_account(db_path, config["account"]["starting_balance"])
    storage.ensure_worker_settings(db_path, config["poll_interval_minutes"] * 60)
    storage.ensure_autopilot(db_path, default_enabled=True)  # continuous by default
    storage.ensure_calibration(db_path)  # auto-calibration on by default (dashboard toggle)

    if args.once:
        run_once(config, db_path)
        return

    # Refuse to start if another worker is already polling. Two workers racing
    # is how bad pre-market data kept getting regenerated and how snapshots got
    # written twice - the second instance must exit, not run alongside. The
    # socket is held in a local so it lives as long as the process.
    try:
        _lock = single_instance.acquire()  # noqa: F841 - held for process lifetime
    except single_instance.AlreadyRunning as exc:
        print(f"another 0DTE worker is already running - exiting. ({exc})")
        sys.exit(0)
    print(f"worker started (pid {os.getpid()}) - holding single-instance lock")
    run_loop(config, db_path)


if __name__ == "__main__":
    main()
