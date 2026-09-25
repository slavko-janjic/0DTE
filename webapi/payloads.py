"""Read payloads for the web UI: one function per endpoint, each returning a
plain JSON-safe dict built from SQLite only.

Every function here is the same computation the Streamlit dashboard does in its
matching section - the recipes were lifted from dashboard.py so both front ends
report identical numbers. Nothing in this module touches the network, so the
endpoints stay fast and the functions stay unit-testable with a temp database.
"""
from __future__ import annotations

import calendar as _cal
import json
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from analytics import accuracy, charting
from analytics.spreads import cheapest_windows, spread_by_minute_bucket, spread_summary
from paper_trading.engine import (
    calculate_pnl, daily_realized_pnl, explain_auto_decision, month_calendar_cells,
    summarize_pnl, trade_events,
)
from paper_trading.shadow import strategy_edge, strategy_scorecard
from signals import day_setup as day_setup_mod
from storage import db as storage
from worker import is_market_open, minutes_since_market_open, minutes_to_market_close

CATEGORY_INFO = {
    "technicals": ("Technicals", "Is the short-term price trend pointing up or down right now "
                                 "(momentum, RSI, price vs. volume-weighted average)."),
    "order_flow": ("Order flow", "Are more contracts trading as calls or puts, and where would "
                                 "price 'settle' to hurt the most option holders (max pain)."),
    "volatility_regime": ("Volatility regime", "Is the overall market calm or fearful right now "
                                               "(VIX/VVIX) - fear tends to precede lower prices."),
}

GAMMA_BADGE = {
    "positive": ("Long gamma · rangebound",
                 "dealers fade moves; breakouts tend to fail, so autopilot wants extra "
                 "confidence here", "amber"),
    "negative": ("Short gamma · trending",
                 "dealers chase moves; breakouts tend to follow through", "up"),
    "neutral": ("Neutral gamma", "no strong dealer-hedging lean either way", "mut"),
}

TONE_BY_DIRECTION = {"bullish": "up", "bearish": "down", "neutral": "flat"}
LEAN_BY_DIRECTION = {"bullish": "call", "bearish": "put"}

# per-trade profit-target / stop-loss choices offered in the UI; None = off
EXIT_PCT_OPTIONS = [None, 5, 10, 20, 30, 40, 50]


# --- small shared helpers -------------------------------------------------

def _tz(config: dict) -> ZoneInfo:
    return ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _num(value):
    """JSON can't carry NaN/Infinity; pandas and statistics both produce them."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        return None
    return value


def display_confidence(snap) -> tuple[float, float | None]:
    """(shown, raw_if_calibrated) - the worker's calibrated confidence when it
    exists and differs from the raw one, else the raw value with no caption."""
    raw = snap["confidence"]
    calibrated = snap["calibrated_confidence"]
    if calibrated is not None and calibrated != raw:
        return calibrated, raw
    return raw, None


def _age_text(age: timedelta) -> str:
    minutes = int(age.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60}h {minutes % 60}m ago"


def _is_today(iso_ts: str | None, tz: ZoneInfo, today: date) -> bool:
    return bool(iso_ts) and datetime.fromisoformat(iso_ts).astimezone(tz).date() == today


def _rows(cursor_rows) -> list[dict]:
    return [dict(row) for row in cursor_rows]


# --- header: clock, worker health, wallet, sentiment ----------------------

def market_clock(config: dict, now: datetime | None = None) -> dict:
    """Session progress for the top bar: how far through the trading day we are
    and how long is left (the mockup's thin progress rail)."""
    tz = _tz(config)
    now = (now or datetime.now(tz)).astimezone(tz)
    open_now = is_market_open(config, now)
    since_open = minutes_since_market_open(config, now)
    to_close = minutes_to_market_close(config, now)
    session_minutes = since_open + to_close

    if open_now and session_minutes > 0:
        progress = max(0.0, min(1.0, since_open / session_minutes))
        hours, minutes = divmod(int(max(0, to_close)), 60)
        label = f"{hours}h {minutes}m to close" if hours else f"{minutes}m to close"
        urgency = "urgent" if to_close <= 30 else ("warm" if to_close <= 60 else "calm")
    else:
        progress, label, urgency = 0.0, "Market closed", "calm"

    return {
        "open": open_now,
        "progress": progress,
        "minutes_to_close": to_close,
        "minutes_since_open": since_open,
        "label": label,
        "urgency": urgency,
        "now_et": now.strftime("%H:%M"),
        "date_et": now.date().isoformat(),
    }


def worker_health(db_path: str) -> dict:
    """Heartbeat age -> up/down. A silent worker outage once cost ~2 weeks of
    data, so this is surfaced on every page, not buried in a status tab."""
    heartbeat = storage.get_heartbeat(db_path)
    poll = storage.get_poll_interval_seconds(db_path)
    stale_after = max(poll * 3, 300)  # a couple of missed cycles' grace

    if heartbeat is None:
        return {"status": "never", "message": "Worker has never run against this database - "
                                              "no data is being collected."}
    age = heartbeat["age_seconds"]
    if age <= stale_after:
        ago = f"{int(age)}s ago" if age < 90 else f"{int(age // 60)}m ago"
        return {"status": "up", "age_seconds": age, "pid": heartbeat["pid"],
                "note": heartbeat["note"], "message": f"Worker alive · pid {heartbeat['pid']} · "
                                                      f"last beat {ago}"}
    last = f"{age / 60:.0f} min ago" if age < 3600 else f"{age / 3600:.1f} h ago"
    return {"status": "down", "age_seconds": age, "pid": heartbeat["pid"],
            "note": heartbeat["note"],
            "message": f"Worker looks DOWN - last heartbeat {last} (pid {heartbeat['pid']}). "
                       f"Restart the 0DTE-Worker task."}


def wallet(db_path: str, config: dict) -> dict:
    open_rows = storage.get_open_positions(db_path)
    closed_rows = storage.get_closed_positions(db_path)
    pnl = summarize_pnl(open_rows, closed_rows,
                        tz_name=config["market_hours"].get("timezone", "America/New_York"))
    return {
        "balance": storage.get_balance(db_path),
        "starting_balance": config["account"]["starting_balance"],
        "realized_today": pnl["realized_today"],
        "realized_total": pnl["realized_total"],
        "unrealized_open": pnl["unrealized_open"],
        "open_exposure": sum(row["cost_basis"] for row in open_rows),
        "open_count": len(open_rows),
    }


def sentiment_strip(db_path: str, config: dict) -> list[dict]:
    strip = []
    for ticker in config["tickers"]:
        snap = storage.get_latest_signal(db_path, ticker)
        if snap is None:
            strip.append({"ticker": ticker, "direction": None, "tone": "flat",
                          "confidence": None})
            continue
        confidence, _raw = display_confidence(snap)
        strip.append({
            "ticker": ticker,
            "direction": snap["direction"],
            "tone": TONE_BY_DIRECTION.get(snap["direction"], "flat"),
            "confidence": confidence,
        })
    return strip


def autopilot_today(db_path: str, config: dict) -> dict:
    """Mode + today's auto activity - the bits the header and both autopilot
    cards share."""
    mode, armed_date = storage.get_autopilot_state(db_path)
    tz = _tz(config)
    today = datetime.now(tz).date()
    closed = storage.get_closed_positions(db_path)
    open_rows = storage.get_open_positions(db_path)

    trades_today = sum(1 for row in list(open_rows) + list(closed)
                       if row["opened_by"] == "auto" and _is_today(row["entry_time"], tz, today))
    pnl_today = sum((row["pnl"] or 0.0) for row in closed
                    if row["opened_by"] == "auto" and _is_today(row["exit_time"], tz, today))

    ap_cfg = config.get("autopilot", {})
    limit = ap_cfg.get("daily_loss_limit_pct", 10) / 100.0 * config["account"]["starting_balance"]
    cap = (ap_cfg.get("max_entries_per_session", 1)
           if mode == "day" and ap_cfg.get("tactic", "opening_range") == "opening_range"
           else ap_cfg.get("max_trades_per_day", 4))
    return {
        "mode": mode,
        "armed_date": armed_date,
        "trades_today": trades_today,
        "trades_cap": cap,
        "pnl_today": pnl_today,
        "circuit_breaker": pnl_today <= -limit,
    }


def overview(db_path: str, config: dict) -> dict:
    """One call for everything that is on screen no matter which page is open,
    including the armed/open state the ARMED alert edge-triggers on."""
    clock = market_clock(config)
    autopilot = autopilot_today(db_path, config)
    intents = autopilot_intents(db_path, config, mode=autopilot["mode"], clock=clock)
    autopilot["armed"] = [
        {"ticker": intent["ticker"], "lean": intent["lean"],
         "confidence_pct": intent["confidence_pct"]}
        for intent in intents if intent["would_enter"]
    ]
    autopilot["auto_open"] = [
        {"id": row["id"], "ticker": row["ticker"], "option_type": row["option_type"],
         "strike": row["strike"]}
        for row in storage.get_open_positions(db_path) if row["opened_by"] == "auto"
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": sentiment_strip(db_path, config),
        "wallet": wallet(db_path, config),
        "worker": worker_health(db_path),
        "autopilot": autopilot,
        "market": clock,
    }


# --- signals page ---------------------------------------------------------

def _day_setup_payload(db_path: str, config: dict, ticker: str, on_date: date | None = None) -> dict | None:
    tz = _tz(config)
    day = (on_date or datetime.now(tz).date()).isoformat()
    setup = storage.get_day_setup(db_path, ticker, day)
    if setup is None:
        return None
    catalysts = setup.get("catalysts") or []
    return {
        "date": day,
        "gap_pct": _num(setup.get("gap_pct")),
        "opening_bias": _num(setup.get("opening_bias")),
        "prior_close": _num(setup.get("prior_close")),
        "prior_high": _num(setup.get("prior_high")),
        "prior_low": _num(setup.get("prior_low")),
        "overnight_high": _num(setup.get("overnight_high")),
        "overnight_low": _num(setup.get("overnight_low")),
        "catalysts": [{"label": c.get("label"), "time": c.get("time"),
                       "impact": c.get("impact")} for c in catalysts],
    }


def signal_payload(db_path: str, config: dict, ticker: str) -> dict:
    """Everything the Signals page's three top cards need for one ticker."""
    balance = storage.get_balance(db_path)
    default_amount = round(balance * config["account"]["risk_per_trade_pct"] / 100.0, 2)
    base = {
        "ticker": ticker,
        "available": False,
        "day_setup": _day_setup_payload(db_path, config, ticker),
        "default_amount": default_amount,
        "exit_pct_options": EXIT_PCT_OPTIONS,
        "autopilot_targets": {
            "profit_target_pct": config.get("autopilot", {}).get("profit_target_pct", 50),
            "stop_loss_pct": config.get("autopilot", {}).get("stop_loss_pct", -35),
        },
    }

    snap = storage.get_latest_signal(db_path, ticker)
    if snap is None:
        base["message"] = "No signal yet - make sure the worker is running."
        return base

    direction = snap["direction"]
    confidence, raw_confidence = display_confidence(snap)
    timestamp = datetime.fromisoformat(snap["timestamp"])
    age = datetime.now(timezone.utc) - timestamp
    stale_after = max(3 * storage.get_poll_interval_seconds(db_path), 900)
    local_time = timestamp.astimezone(_tz(config))

    gamma = None
    if snap["gamma_regime"]:
        label, blurb, tone = GAMMA_BADGE[snap["gamma_regime"]]
        score = snap["gamma_score"]
        gamma = {
            "regime": snap["gamma_regime"],
            "label": label + (f" ({score:+.2f})" if score is not None else ""),
            "blurb": blurb,
            "tone": tone,
            "score": _num(score),
        }

    streak = None
    if snap["direction_streak"] and direction != "neutral":
        poll_minutes = max(1, storage.get_poll_interval_seconds(db_path) // 60)
        polls = snap["direction_streak"]
        streak = {"polls": polls, "minutes": polls * poll_minutes,
                  "label": f"{direction} {polls * poll_minutes} min"}

    weights = storage.effective_weights(db_path, config, ticker)
    subscores = []
    for key, value in json.loads(snap["subscores_json"]).items():
        name, description = CATEGORY_INFO.get(key, (key, ""))
        subscores.append({
            "key": key,
            "name": name,
            "description": description,
            "weight_pct": weights.get(key, 0) * 100,
            "value": _num(value),
            # -1..+1 mapped onto a 0-100 meter; below the midpoint reads as "lo"
            "meter_pct": None if value is None else (float(value) + 1.0) / 2.0 * 100.0,
            "tone": "lo" if (value is not None and value < 0) else "hi",
        })

    spot = _num(snap["spot_price"])
    base.update({
        "available": True,
        "direction": direction,
        "tone": TONE_BY_DIRECTION.get(direction, "flat"),
        "confidence": confidence,
        "raw_confidence": raw_confidence,
        "composite_score": _num(snap["composite_score"]),
        "recommendation": snap["recommendation"],
        "spot_price": spot,
        "timestamp": snap["timestamp"],
        "time_et": local_time.strftime("%H:%M"),
        "day_et": local_time.strftime("%a %H:%M"),
        "age_seconds": age.total_seconds(),
        "age_text": _age_text(age),
        "stale": age.total_seconds() > stale_after,
        "gamma": gamma,
        "streak": streak,
        "subscores": subscores,
        "suggested_type": LEAN_BY_DIRECTION.get(direction),
        "suggested_strike": round(spot) if spot is not None else None,
    })
    return base


# The chart is a 720-unit-wide SVG: a 30-day "all" range is ~7,500 points per
# ticker, which is payload and DOM for no visible gain. Longer ranges are
# thinned evenly (a full session, ~390 points, never is).
MAX_CHART_POINTS = 800


def _thin(items: list, limit: int) -> list:
    """Every k-th item so at most `limit` remain, always keeping the newest."""
    if len(items) <= limit:
        return items
    step = len(items) / limit
    thinned = [items[int(i * step)] for i in range(limit)]
    thinned[-1] = items[-1]
    return thinned


def _window_info(config: dict) -> dict:
    """What the accuracy numbers cover, so the UI can say so."""
    start = accuracy.composite_start(config)
    return {"window_days": config.get("accuracy_window_days", accuracy.DEFAULT_WINDOW_DAYS),
            "composite_since": start.date().isoformat() if start else None}


def signal_history(db_path: str, config: dict, ticker: str, range_key: str = "session") -> dict:
    """Points for the price + composite-score chart, plus the accuracy readout.

    The range filters the chart only: accuracy always covers the current
    composite's graded calls within the look-back window, because that is
    what it measures. "session" defaults to the most
    recent day that HAS data rather than the calendar today - otherwise the
    chart is empty every evening and weekend, which reads as breakage.
    """
    tz = _tz(config)
    horizon = config["accuracy_horizon_minutes"]
    payload = {"ticker": ticker, "range": range_key, "points": [], "events": [],
               "levels": [], "sessions": [], "horizon_minutes": horizon,
               "accuracy_pct": None, "graded_count": 0, "daily": [],
               **_window_info(config)}
    # same cached grading the Tuning page uses: accuracy is over the current
    # composite only, the chart can show the whole look-back window
    snapshots, evaluated, _categories, _bands = _analysis(db_path, config, ticker)
    if not snapshots:
        payload["message"] = "Not enough history yet - check back after a few poll cycles."
        return payload

    payload["accuracy_pct"] = _num(accuracy.overall_accuracy_pct(evaluated))
    payload["graded_count"] = sum(1 for snap in evaluated if snap["evaluated"])
    payload["daily"] = [
        {"date": day["date"], "total": day["total"], "hits": day["hits"],
         "accuracy_pct": _num(day["accuracy_pct"])}
        for day in reversed(accuracy.daily_accuracy_summary(
            evaluated, tz_name=config["market_hours"]["timezone"]))
    ]

    sessions = charting.session_dates(snapshots, tz.key)
    payload["sessions"] = [day.isoformat() for day in sessions]
    now_market = datetime.now(tz)
    cutoff = until = None
    picked_session = sessions[0] if sessions else None

    if range_key == "session" and picked_session is not None:
        close_time = config["market_hours"].get("close", "16:00")
        if picked_session.isoformat() in config.get("market_half_days", []):
            close_time = "13:00"
        start_naive, end_naive = charting.session_window(
            picked_session, config["market_hours"].get("open", "09:30"), close_time)
        cutoff = start_naive.replace(tzinfo=tz)
        until = end_naive.replace(tzinfo=tz)
        payload["session_date"] = picked_session.isoformat()
    elif range_key == "4h":
        cutoff = now_market - timedelta(hours=4)
    elif range_key == "3d":
        cutoff = now_market - timedelta(days=3)

    points = [
        snap for snap in snapshots
        if snap["spot_price"] is not None
        and (cutoff is None or snap["timestamp"] >= cutoff)
        and (until is None or snap["timestamp"] <= until)
    ]
    # a multi-day range needs the date in the label; a single session doesn't
    label_format = "%H:%M" if range_key == "session" else "%m-%d %H:%M"
    points_drawn = _thin(points, MAX_CHART_POINTS)
    payload["points"] = [
        {
            "t": snap["timestamp"].astimezone(tz).strftime(label_format),
            "iso": snap["timestamp"].astimezone(tz).isoformat(),
            "price": _num(snap["spot_price"]),
            "score": _num(snap["composite_score"]),
            "confidence": _num(snap["confidence"]),
            "direction": snap["direction"],
        }
        for snap in points_drawn
    ]

    if points:
        markers = [
            event for event in trade_events(storage.get_open_positions(db_path),
                                            storage.get_closed_positions(db_path), ticker)
            if (cutoff is None or event["time"] >= cutoff)
            and (until is None or event["time"] <= until)
        ]
        payload["events"] = [
            {"iso": event["time"].astimezone(tz).isoformat(), "kind": event["kind"],
             "label": event["label"]}
            for event in markers
        ]

        # Pre-market key levels, but only the ones near the traded range: a level
        # far outside it expands the y-axis and squashes the price line flat.
        setup_day = picked_session or now_market.date()
        setup = storage.get_day_setup(db_path, ticker, setup_day.isoformat())
        if setup:
            candidates = {
                "Prior close": setup.get("prior_close"), "Prior high": setup.get("prior_high"),
                "Prior low": setup.get("prior_low"), "O/N high": setup.get("overnight_high"),
                "O/N low": setup.get("overnight_low"),
            }
            prices = [snap["spot_price"] for snap in points]
            drawable = charting.visible_levels(candidates, price_low=min(prices),
                                               price_high=max(prices))
            payload["levels"] = [{"label": name, "value": _num(value)}
                                 for name, value in drawable.items()]
    else:
        payload["message"] = "No data in this range yet."
    return payload


# --- trades page ----------------------------------------------------------

def position_rows(db_path: str, config: dict, live: dict | None = None) -> list[dict]:
    """Open positions with unrealized P&L. `live` is {position_id: {price, spread_pct}}
    from a fresh chain fetch; without it the worker's last stored price is used."""
    live = live or {}
    rows = []
    for pos in storage.get_open_positions(db_path):
        quote = live.get(pos["id"], {})
        current_price = quote.get("price")
        if current_price is None:
            current_price = pos["current_price"]
        pnl = pnl_pct = None
        if current_price is not None:
            pnl, pnl_pct = calculate_pnl(pos["entry_price"], current_price, pos["contracts"])
        peak_pct = None
        if pos["max_price"] is not None and pos["entry_price"] > 0 \
                and pos["max_price"] > pos["entry_price"]:
            peak_pct = (pos["max_price"] - pos["entry_price"]) / pos["entry_price"] * 100.0
        rows.append({
            "id": pos["id"],
            "ticker": pos["ticker"],
            "option_type": pos["option_type"],
            "strike": pos["strike"],
            "expiration": pos["expiration"],
            "contracts": pos["contracts"],
            "entry_price": pos["entry_price"],
            "current_price": _num(current_price),
            "cost_basis": pos["cost_basis"],
            "pnl": _num(pnl),
            "pnl_pct": _num(pnl_pct),
            "peak_pct": _num(peak_pct),
            # bid-side valuation vs ask-side entry: the round trip's honest cost
            "spread_pct": _num(quote.get("spread_pct")),
            "opened_by": pos["opened_by"],
            "profit_target_pct": pos["profit_target_pct"],
            "stop_loss_pct": pos["stop_loss_pct"],
            "suggested_exit_reason": pos["suggested_exit_reason"],
            "entry_time": pos["entry_time"],
        })
    return rows


def positions_payload(db_path: str, config: dict, live: dict | None = None) -> dict:
    return {"positions": position_rows(db_path, config, live),
            "wallet": wallet(db_path, config),
            "exit_pct_options": EXIT_PCT_OPTIONS}


def trade_history(db_path: str, config: dict, limit: int = 200) -> dict:
    tz = _tz(config)
    rows = []
    for pos in storage.get_closed_positions(db_path)[:limit]:
        exit_time = datetime.fromisoformat(pos["exit_time"]).astimezone(tz) \
            if pos["exit_time"] else None
        reason = pos["exit_reason"] or ""
        rows.append({
            "id": pos["id"],
            "date": exit_time.strftime("%m-%d") if exit_time else "",
            "exit_time": pos["exit_time"],
            "ticker": pos["ticker"],
            "option_type": pos["option_type"],
            "strike": pos["strike"],
            "contracts": pos["contracts"],
            "entry_price": pos["entry_price"],
            "exit_price": _num(pos["exit_price"]),
            "score": _num(pos["entry_composite_score"]),
            "exit_reason": "you closed" if reason == "manual" else reason.replace("_", " "),
            "pnl": _num(pos["pnl"]),
            "opened_by": pos["opened_by"],
        })
    return {"trades": rows}


def calendar_payload(db_path: str, config: dict, year: int | None = None,
                     month: int | None = None) -> dict:
    tz = _tz(config)
    today = datetime.now(tz).date()
    year = year or today.year
    month = month or today.month
    by_day = daily_realized_pnl(storage.get_closed_positions(db_path), tz_name=tz.key)
    weeks = month_calendar_cells(by_day, year, month)
    month_total = sum(pnl for day, pnl in by_day.items()
                      if day.year == year and day.month == month)
    return {
        "year": year,
        "month": month,
        "label": f"{_cal.month_name[month]} {year}",
        "short_label": f"{_cal.month_abbr[month]}",
        "month_total": month_total,
        "weeks": [
            [
                {"day": cell["day"], "in_month": cell["in_month"], "pnl": _num(cell["pnl"]),
                 "today": cell["date"] == today}
                for cell in week
            ]
            for week in weeks
        ],
    }


# --- strategy lab ---------------------------------------------------------

MIN_LAB_TRADES = 20


def lab_payload(db_path: str, config: dict) -> dict:
    strategies = config.get("shadow_strategies", [])
    if not strategies:
        return {"rows": [], "message": "No shadow strategies configured "
                                       "(settings.yaml -> shadow_strategies)."}
    closed = storage.get_closed_shadow_positions(db_path)
    open_counts: dict[str, int] = {}
    for row in storage.get_open_shadow_positions(db_path):
        open_counts[row["strategy"]] = open_counts.get(row["strategy"], 0) + 1

    cards = strategy_scorecard(closed)
    edges = strategy_edge(closed, min_trades=MIN_LAB_TRADES)
    rows = []
    for strategy in strategies:
        name = strategy.get("name", "?")
        card = cards.get(name)
        edge = edges.get(name, {})
        verdict = edge.get("verdict", "no trades yet") if card else "no trades yet"
        rows.append({
            "name": name,
            "trades": card["trades"] if card else 0,
            "win_rate_pct": _num(card["win_rate_pct"]) if card else None,
            "total_pnl": _num(card["total_pnl"]) if card else None,
            "avg_pnl": _num(card["avg_pnl"]) if card else None,
            "profit_factor": _num(card["profit_factor"]) if card else None,
            "max_drawdown": _num(card["max_drawdown"]) if card else None,
            "open": open_counts.get(name, 0),
            "t_stat": _num(edge.get("t_stat")),
            "trades_needed": edge.get("trades_needed"),
            "verdict": verdict,
            "verdict_tone": ("up" if verdict == "EDGE (+)" else
                             "down" if verdict == "EDGE (-)" else "mut"),
        })
    # strongest first among those with data; warming-up rows keep config order
    rows.sort(key=lambda row: (row["trades"] == 0, -(row["total_pnl"] or 0.0)))
    return {"rows": rows, "min_trades": MIN_LAB_TRADES}


# --- cost of trading ------------------------------------------------------

def cost_payload(db_path: str, config: dict, ticker: str) -> dict:
    rows = []
    for name in config["tickers"]:
        quotes = storage.get_quote_history(db_path, name)
        summary = spread_summary(quotes)
        latest = storage.get_latest_quote(db_path, name)
        rows.append({
            "ticker": name,
            "now_pct": _num(latest["spread_pct"]) if latest else None,
            "median_pct": _num(summary["median_pct"]),
            "best_pct": _num(summary["best_pct"]),
            "worst_pct": _num(summary["worst_pct"]),
            "samples": summary["samples"],
        })

    buckets = spread_by_minute_bucket(storage.get_quote_history(db_path, ticker),
                                      bucket_minutes=30)
    payload = {"ticker": ticker, "rows": rows, "curve": [], "cheapest": [],
               "at_open": None, "midday": None}
    if not buckets:
        payload["message"] = (f"No quote history for {ticker} yet - the worker logs one "
                              "per cycle during market hours.")
        return payload

    open_hm = config["market_hours"].get("open", "09:30")
    open_minutes = int(open_hm[:2]) * 60 + int(open_hm[3:5])
    payload["curve"] = [
        {"minutes": bucket["bucket_start"],
         "label": bucket["bucket_label"],
         "clock": f"{(open_minutes + bucket['bucket_start']) // 60 % 24:02d}:"
                  f"{(open_minutes + bucket['bucket_start']) % 60:02d}",
         "median_pct": _num(bucket["median_spread_pct"]),
         "samples": bucket["samples"]}
        for bucket in buckets
    ]
    payload["cheapest"] = [
        {"label": bucket["bucket_label"], "median_pct": _num(bucket["median_spread_pct"])}
        for bucket in cheapest_windows(buckets, top=3, min_samples=5)
    ]
    payload["at_open"] = payload["curve"][0]["median_pct"]
    cheapest_point = min((point for point in payload["curve"]
                          if point["median_pct"] is not None),
                         key=lambda point: point["median_pct"], default=None)
    if cheapest_point:
        payload["midday"] = cheapest_point["median_pct"]
        payload["midday_label"] = cheapest_point["clock"]
    return payload


# --- autopilot ------------------------------------------------------------

def autopilot_intents(db_path: str, config: dict, mode: str | None = None,
                      clock: dict | None = None, now: datetime | None = None,
                      open_rows: list | None = None, closed_rows: list | None = None) -> list[dict]:
    """The bot's own entry decision per whitelisted ticker, dry-run.

    `explain_auto_decision` is the function the worker itself calls, so this is
    the real intent rather than a re-implementation of it. Empty while the
    market is closed or autopilot is off - there is nothing it would do.
    """
    ap_cfg = config.get("autopilot", {})
    tickers = ap_cfg.get("tickers") or config["tickers"]
    tz = _tz(config)
    now = now or datetime.now(tz)
    clock = clock or market_clock(config, now)
    if mode is None:
        mode, _armed = storage.get_autopilot_state(db_path)
    if not clock["open"] or mode == "off":
        return []

    open_rows = storage.get_open_positions(db_path) if open_rows is None else open_rows
    closed_rows = storage.get_closed_positions(db_path) if closed_rows is None else closed_rows
    catalysts = day_setup_mod.catalysts_for_date(
        config.get("market_catalysts", []), now.date(), tz.key)
    minutes_to_catalyst = day_setup_mod.minutes_to_next_catalyst(catalysts, now, tz.key)
    profit_target = ap_cfg.get("profit_target_pct", 50)
    stop_loss = ap_cfg.get("stop_loss_pct", -35)

    intents = []
    for ticker in tickers:
        snap = storage.get_latest_signal(db_path, ticker)
        if snap is None:
            intents.append({"ticker": ticker, "would_enter": False, "lean": None,
                            "confidence_pct": None, "direction": None, "blocker": None,
                            "message": "no signal yet this session."})
            continue
        confidence, _raw = display_confidence(snap)
        intent = explain_auto_decision(
            ticker=ticker, direction=snap["direction"], confidence_pct=confidence or 0.0,
            minutes_since_open=clock["minutes_since_open"],
            minutes_to_close=clock["minutes_to_close"],
            open_rows=open_rows, closed_rows=closed_rows, autopilot_cfg=ap_cfg,
            starting_balance=config["account"]["starting_balance"], now=now,
            tz_name=tz.key, minutes_to_catalyst=minutes_to_catalyst,
            gamma_regime=snap["gamma_regime"],
        )
        intents.append({
            "ticker": ticker,
            "direction": intent.direction,
            "lean": intent.lean,
            "confidence_pct": _num(intent.confidence_pct),
            "min_confidence_pct": _num(intent.min_confidence_pct),
            "would_enter": intent.would_enter,
            "blocker": intent.blocker,
            "message": (f"<b>ARMED</b> — would buy <b>{intent.lean}s</b> now. "
                        f"Mirror: ATM 0DTE {intent.lean}, "
                        f"+{profit_target:.0f}% / {stop_loss:.0f}%."
                        if intent.would_enter else
                        f"standing down: {intent.blocker}"),
        })
    return intents


def autopilot_payload(db_path: str, config: dict) -> dict:
    """Mode, per-ticker intent (the bot's own dry-run decision), record, config.

    `explain_auto_decision` is the same function the worker calls, so what this
    shows is the bot's real intent - not a re-implementation of it.
    """
    ap_cfg = config.get("autopilot", {})
    tickers = ap_cfg.get("tickers") or config["tickers"]
    tz = _tz(config)
    now = datetime.now(tz)
    today = autopilot_today(db_path, config)
    clock = market_clock(config, now)

    open_rows = storage.get_open_positions(db_path)
    closed_rows = storage.get_closed_positions(db_path)

    intents = autopilot_intents(db_path, config, mode=today["mode"], clock=clock, now=now,
                                open_rows=open_rows, closed_rows=closed_rows)

    auto_pnls = [row["pnl"] or 0.0 for row in closed_rows if row["opened_by"] == "auto"]
    record = {"trades": len(auto_pnls)}
    if auto_pnls:
        wins = sum(1 for pnl in auto_pnls if pnl > 0)
        record.update({
            "total_pnl": sum(auto_pnls),
            "win_rate_pct": wins / len(auto_pnls) * 100.0,
            "best": max(auto_pnls),
            "worst": min(auto_pnls),
            "avg": sum(auto_pnls) / len(auto_pnls),
        })

    open_hm = config["market_hours"].get("open", "09:30")
    open_minutes = int(open_hm[:2]) * 60 + int(open_hm[3:5])
    start = open_minutes + ap_cfg.get("decision_start_minutes", 30)
    end = open_minutes + ap_cfg.get("decision_end_minutes", 90)
    window_open = clock["open"] and (ap_cfg.get("decision_start_minutes", 30)
                                     <= clock["minutes_since_open"]
                                     <= ap_cfg.get("decision_end_minutes", 90))

    return {
        "mode": today["mode"],
        "armed_date": today["armed_date"],
        "today": today,
        "market": clock,
        "window": {
            "open": window_open,
            "label": f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d} ET",
        },
        "intents": intents,
        "record": record,
        "wallet": wallet(db_path, config),
        "auto_pnl_today": today["pnl_today"],
        "config": [
            {"label": "Trades", "value": " · ".join(tickers)},
            {"label": "Tactic", "value": ap_cfg.get("tactic", "opening_range").replace("_", " ")},
            {"label": "Window",
             "value": f"{ap_cfg.get('decision_start_minutes', 30):.0f}–"
                      f"{ap_cfg.get('decision_end_minutes', 90):.0f} min"},
            {"label": "Confidence gate", "value": f"≥ {ap_cfg.get('min_confidence_pct', 55):.0f}%"},
            {"label": "Exits", "value": f"+{ap_cfg.get('profit_target_pct', 50):.0f}% / "
                                        f"{ap_cfg.get('stop_loss_pct', -35):.0f}%"},
            {"label": "Per session", "value": f"{ap_cfg.get('max_entries_per_session', 1)} / ticker"},
            {"label": "Daily loss limit",
             "value": f"{ap_cfg.get('daily_loss_limit_pct', 10):.0f}% of start"},
        ],
    }


# --- tuning: calibration, per-signal accuracy, weight suggestions ---------

# The accuracy grading is O(n^2) over the full history and the Tuning page polls
# like every other page, so the result is memoised per (ticker, history
# fingerprint) exactly as the Streamlit fragment cached it. A new snapshot
# changes the fingerprint and the analysis recomputes.
_ANALYSIS_CACHE: dict[str, tuple] = {}


def _analysis(db_path: str, config: dict, ticker: str):
    """(snapshots, evaluated, category_results, confidence_bands) for a ticker.

    snapshots and category_results span the whole look-back window (each
    signal's own score is stored, so it grades fine across composite changes);
    evaluated and the bands are the CURRENT composite only. Categories are
    limited to the live signals in config, so removed ones don't linger."""
    rows = storage.get_signal_history(
        db_path, ticker, since=accuracy.history_window_start(config).isoformat())
    if not rows:
        return [], [], {}, []
    horizon = config["accuracy_horizon_minutes"]
    fingerprint = (len(rows), rows[0]["timestamp"], rows[-1]["timestamp"], horizon,
                   tuple(_window_info(config).values()))
    cached = _ANALYSIS_CACHE.get(ticker)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    snapshots = accuracy.history_snapshots(rows)
    evaluated = accuracy.evaluate_signal_accuracy(
        accuracy.since_composite(snapshots, accuracy.composite_start(config)), horizon)
    categories = {category: result
                  for category, result in accuracy.evaluate_category_accuracy(
                      snapshots, horizon).items()
                  if category in config["weights"]}
    bands = accuracy.confidence_calibration(evaluated)
    result = (snapshots, evaluated, categories, bands)
    _ANALYSIS_CACHE[ticker] = (fingerprint, result)
    return result


def _context_rows(evaluated: list, context_fn, order: list[str]) -> list[dict]:
    buckets = accuracy.bucket_evaluated(evaluated, context_fn)
    return [
        {"label": label, "graded": buckets[label]["graded"],
         "accuracy_pct": _num(buckets[label]["accuracy_pct"])}
        for label in order
        if label in buckets and buckets[label]["graded"]
    ]


def suggested_weights(db_path: str, config: dict, ticker: str) -> dict | None:
    """The accuracy-based weight suggestion for one ticker, or None when no
    category has enough independent observations to justify moving anything."""
    _snapshots, _evaluated, categories, _bands = _analysis(db_path, config, ticker)
    if not categories:
        return None
    active = storage.effective_weights(db_path, config, ticker)
    return accuracy.suggest_weights(
        categories, active, min_graded=config.get("weight_suggestion_min_graded", 10))


EVENT_LABELS = {
    "weights_nudged": "weights nudged", "inversion_added": "inverted",
    "inversion_removed": "un-inverted", "confidence_map_updated": "confidence remapped",
    "reverted": "reverted",
}


def _event_detail(kind: str, detail: dict) -> str:
    if kind in ("inversion_added", "inversion_removed"):
        category = detail.get("category", "")
        name = CATEGORY_INFO.get(category, (category, ""))[0]
        graded = detail.get("accuracy_pct")
        return name + (f" ({graded:.0f}% acc)" if graded is not None else "")
    if kind == "confidence_map_updated":
        return f"{len(detail.get('bands', []))} band(s)"
    return ""


def calibration_payload(db_path: str, config: dict, ticker: str) -> dict:
    """Everything the Tuning page shows: what the nightly self-calibration has
    done, how well-calibrated the confidence numbers are, which individual
    signals are actually calling direction right, and the weight suggestion
    that follows from it."""
    cal_cfg = config.get("calibration", {})
    min_graded = config.get("weight_suggestion_min_graded", 10)
    horizon = config["accuracy_horizon_minutes"]
    _snapshots, evaluated, categories, bands = _analysis(db_path, config, ticker)
    active = storage.effective_weights(db_path, config, ticker)
    override = storage.get_weight_overrides(db_path, ticker)

    graded_categories = {key: value for key, value in categories.items()
                         if value["graded_count"] > 0}
    category_rows = [
        {
            "key": key,
            "name": CATEGORY_INFO.get(key, (key, ""))[0],
            "description": CATEGORY_INFO.get(key, (key, ""))[1],
            "weight_pct": active.get(key, 0) * 100,
            "graded": value["graded_count"],
            "independent": value.get("independent_count"),
            "accuracy_pct": _num(value["accuracy_pct"]),
            "inverted": key in storage.get_inversions(db_path, ticker),
        }
        for key, value in sorted(graded_categories.items(),
                                 key=lambda item: item[1]["accuracy_pct"] or 0, reverse=True)
    ]

    suggestion = None
    suggested = accuracy.suggest_weights(categories, active, min_graded=min_graded) \
        if categories else None
    if suggested is not None:
        suggestion = {
            "rows": [
                {"key": key, "name": CATEGORY_INFO.get(key, (key, ""))[0],
                 "current_pct": active.get(key, 0) * 100,
                 "suggested_pct": suggested.get(key, 0) * 100}
                for key in active
            ],
            "applied_at": override[1] if override is not None else None,
            "source": "applied suggestion" if override is not None else "config defaults",
        }

    tz = _tz(config)
    return {
        "ticker": ticker,
        "enabled": storage.get_calibration_enabled(db_path),
        "last_run": storage.get_last_calibration_date(db_path),
        "learning_rate_pct": cal_cfg.get("learning_rate", 0.25) * 100,
        "min_graded": min_graded,
        "horizon_minutes": horizon,
        **_window_info(config),
        "graded_count": sum(1 for snap in evaluated if snap["evaluated"]),
        "overall_accuracy_pct": _num(accuracy.overall_accuracy_pct(evaluated)),
        "inversions": [{"key": key, "name": CATEGORY_INFO.get(key, (key, ""))[0]}
                       for key in storage.get_inversions(db_path, ticker)],
        "inversion_candidates": [
            {"key": key, "name": CATEGORY_INFO.get(key, (key, ""))[0]}
            for key in accuracy.inversion_candidates(categories, min_graded=min_graded)
        ],
        "confidence_bands": [
            {"band": band["band"], "count": band["count"],
             "observed_accuracy_pct": _num(band["observed_accuracy_pct"])}
            for band in bands
        ],
        "categories": category_rows,
        "context": {
            "time_of_day": _context_rows(
                evaluated, lambda snap: accuracy.context_time_of_day(snap, tz.key),
                ["morning", "midday", "afternoon"]),
            "volatility": _context_rows(
                evaluated, accuracy.context_volatility_regime, ["calm", "stressed"]),
            "streak": _context_rows(
                evaluated, accuracy.context_direction_streak,
                ["fresh (1-3)", "building (4-15)", "sustained (16+)"]),
        },
        "suggestion": suggestion,
        "events": [
            {
                "when": datetime.fromisoformat(event["created_at"]).strftime("%m-%d %H:%M"),
                "ticker": event["ticker"],
                "action": EVENT_LABELS.get(event["kind"], event["kind"]),
                "detail": _event_detail(event["kind"], json.loads(event["detail_json"])),
            }
            for event in storage.get_calibration_events(db_path, limit=15)
        ],
    }
