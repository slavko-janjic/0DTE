"""Signal research lab CLI - does a signal improve the autopilot's 10:00 trade?

    python research.py calibrate          # option-price model, from the app's own records
    python research.py validate-label     # does the simulated trade match real shadow trades?
    python research.py fetch              # download / update SPY+QQQ minute bars (Alpaca keys)
    python research.py run [--report SIGNAL_RESEARCH.md]

Spec: docs/superpowers/specs/2026-10-19-signal-research-lab-design.md.
Reads the live database read-only. `fetch` and `calibrate` write only to the
history cache beside the database (<data dir>/history), never to the database.
"""
import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics import option_sim, session_lab
from config import database_path, load_settings
from data import history

TICKERS = ("SPY", "QQQ")
ET = ZoneInfo("America/New_York")
QUOTE_LAG_MINUTES = 15          # Yahoo option quotes trail the share price (measured below)
CALIBRATION_FILE = "calibration.json"


def _paths(config: dict) -> tuple[str, Path]:
    db = database_path(config)
    return db, history.history_dir(Path(db).parent)


def _connect_ro(db: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _moment(ts: str) -> tuple[date, float]:
    """(market date, minutes since the 09:30 open) for an ISO timestamp."""
    local = datetime.fromisoformat(ts).astimezone(ET)
    return local.date(), local.hour * 60 + local.minute + local.second / 60 - (9 * 60 + 30)


def _spot_paths(con: sqlite3.Connection) -> dict:
    """{(ticker, day): {minute: spot}} from the worker's per-poll snapshots."""
    paths: dict = defaultdict(dict)
    for row in con.execute("SELECT ticker, timestamp, spot_price FROM signal_snapshots "
                           "WHERE ticker IN ('SPY','QQQ') AND spot_price IS NOT NULL"):
        day, minute = _moment(row["timestamp"])
        if 0 <= minute <= option_sim.SESSION_MINUTES:
            paths[(row["ticker"], day)][int(minute)] = row["spot_price"]
    return paths


def _vix_open(cache: Path) -> dict:
    vix = history.load_vix(cache)
    if not vix:
        history.update_vix(cache)
        vix = history.load_vix(cache)
    return {d: o for d, (o, _) in vix.items()}


def _lag_profile(con: sqlite3.Connection, paths: dict, ticker: str) -> list[tuple[int, float, int]]:
    """Correlation of 1-minute option-quote changes with the share-price change
    `lag` minutes earlier. The peak is the quote delay."""
    quotes = con.execute("SELECT timestamp, strike, mid FROM quote_snapshots WHERE ticker=? "
                         "AND mid IS NOT NULL ORDER BY timestamp", (ticker,)).fetchall()
    pairs = []
    for a, b in zip(quotes, quotes[1:]):
        (da, ma), (db_, mb) = _moment(a["timestamp"]), _moment(b["timestamp"])
        if da == db_ and a["strike"] == b["strike"] and 0.5 <= mb - ma <= 2.5:
            pairs.append((da, int(ma), int(mb), b["mid"] - a["mid"]))
    out = []
    for lag in (0, 5, 10, 14, 15, 16, 20):
        xs, ys = [], []
        for day, m0, m1, dmid in pairs:
            path = paths.get((ticker, day), {})
            s0, s1 = option_sim.spot_at(path, m0 - lag, 2), option_sim.spot_at(path, m1 - lag, 2)
            if s0 is not None and s1 is not None:
                xs.append(s1 - s0)
                ys.append(dmid)
        if len(xs) > 50:
            out.append((lag, statistics.correlation(xs, ys), len(xs)))
    return out


def cmd_calibrate(config: dict) -> None:
    db, cache = _paths(config)
    con = _connect_ro(db)
    paths = _spot_paths(con)
    vix_open = _vix_open(cache)
    half_days = {date.fromisoformat(d) for d in config.get("market_half_days", [])}
    print("Option-quote delay (correlation of quote changes with the share-price change "
          "N minutes earlier):")
    result = {"lag_minutes": QUOTE_LAG_MINUTES, "k": {}, "half_spread": {}, "samples": {},
              "created": date.today().isoformat()}
    for ticker in TICKERS:
        profile = _lag_profile(con, paths, ticker)
        print(f"  {ticker}: " + "  ".join(f"{lag}m {r:+.2f}" for lag, r, _ in profile))
        ratios, spreads = [], []
        for row in con.execute("SELECT timestamp, strike, bid, ask, mid FROM quote_snapshots "
                               "WHERE ticker=? AND mid IS NOT NULL AND minutes_since_open >= 30 "
                               "AND minutes_since_open < 36", (ticker,)):
            day, minute = _moment(row["timestamp"])
            if day in half_days or day not in vix_open:
                continue
            seen = minute - QUOTE_LAG_MINUTES        # the moment the quote actually reflects
            spot = option_sim.spot_at(paths.get((ticker, day), {}), int(seen), 2)
            if spot is None:
                continue
            iv = option_sim.implied_vol(row["mid"], spot, row["strike"],
                                        option_sim.years_to_close(seen), "call")
            if iv is not None:
                ratios.append(iv / (vix_open[day] / 100.0))
            if row["bid"] and row["ask"] and row["ask"] >= row["bid"]:
                spreads.append(row["ask"] - row["bid"])
        if not ratios:
            print(f"  {ticker}: no usable quotes - can't calibrate")
            continue
        quartiles = statistics.quantiles(ratios, n=4)
        result["k"][ticker] = statistics.median(ratios)
        result["half_spread"][ticker] = statistics.median(spreads) / 2 if spreads else 0.0
        result["samples"][ticker] = len(ratios)
        print(f"  {ticker}: 0DTE implied vol at 10:00 = {result['k'][ticker]:.2f} x VIX "
              f"(middle half {quartiles[0]:.2f}-{quartiles[2]:.2f}, {len(ratios)} quotes); "
              f"typical spread ${2 * result['half_spread'][ticker]:.3f}")
    cache.mkdir(parents=True, exist_ok=True)
    (cache / CALIBRATION_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved {cache / CALIBRATION_FILE}")


def _load_calibration(cache: Path) -> dict:
    path = cache / CALIBRATION_FILE
    if not path.exists():
        raise SystemExit("No calibration yet - run: python research.py calibrate")
    return json.loads(path.read_text(encoding="utf-8"))


def _exit_class(reason: str) -> str:
    return {"profit_target": "target", "stop_loss": "stop"}.get(reason, "other")


def validate_trades(config: dict, con: sqlite3.Connection, paths: dict, vix_open: dict,
                    calibration: dict) -> list[dict]:
    """Every closed SPY/QQQ shadow trade the simulator can replay, with the
    simulated result at the app's 15-minute quote delay and at none."""
    exits = {s["name"]: s.get("exit", {}) for s in config.get("shadow_strategies", [])}
    out = []
    for row in con.execute("SELECT * FROM shadow_positions WHERE ticker IN ('SPY','QQQ') "
                           "AND status='closed' AND pnl_pct IS NOT NULL"):
        exit_cfg = exits.get(row["strategy"])
        if exit_cfg is None or row["exit_reason"] == "signal_reversal":
            continue
        day, minute = _moment(row["entry_time"])
        if row["expiration"] != day.isoformat() or day not in vix_open:
            continue
        k = calibration["k"].get(row["ticker"])
        path = paths.get((row["ticker"], day))
        if not k or not path:
            continue
        sigma = k * vix_open[day] / 100.0
        half = calibration["half_spread"].get(row["ticker"], 0.0)
        sims = {}
        for lag in (QUOTE_LAG_MINUTES, 0):
            sims[lag] = option_sim.simulate_trade(path, int(minute), row["strike"],
                                                  row["option_type"], sigma, exit_cfg,
                                                  half_spread=half, lag_minutes=lag)
        if any(s is None for s in sims.values()):
            continue
        out.append({"strategy": row["strategy"], "real_ret": row["pnl_pct"],
                    "real_exit": _exit_class(row["exit_reason"]), "real_entry": row["entry_price"],
                    "sim": sims})
    return out


def cmd_validate(config: dict) -> bool:
    db, cache = _paths(config)
    con = _connect_ro(db)
    calibration = _load_calibration(cache)
    trades = validate_trades(config, con, _spot_paths(con), _vix_open(cache), calibration)
    if not trades:
        print("No replayable shadow trades.")
        return False
    print(f"Replayed {len(trades)} closed SPY/QQQ shadow trades "
          f"(reversal exits excluded).")
    real = [t["real_ret"] for t in trades]
    verdict = False
    for lag, label in ((QUOTE_LAG_MINUTES, "as the app priced it (15-min quote delay)"),
                       (0, "real-time pricing")):
        sims = [t["sim"][lag] for t in trades]
        match = sum(1 for t, s in zip(trades, sims) if _exit_class(s.exit_reason) == t["real_exit"])
        mean_sim = statistics.fmean(s.ret_pct for s in sims)
        entry_err = statistics.median(abs(s.entry_price - t["real_entry"]) / t["real_entry"] * 100
                                      for t, s in zip(trades, sims))
        corr = statistics.correlation(real, [s.ret_pct for s in sims])
        print(f"\n  {label}:")
        print(f"    exit matches real (target/stop/other): {match}/{len(trades)} "
              f"= {match / len(trades):.0%}")
        print(f"    mean return: simulated {mean_sim:+.1f}%  vs real {statistics.fmean(real):+.1f}%"
              f"   (correlation {corr:+.2f})")
        print(f"    entry price: median error {entry_err:.0f}%")
        if lag == QUOTE_LAG_MINUTES:
            verdict = (match / len(trades) >= 0.75
                       and abs(mean_sim - statistics.fmean(real)) <= 5.0)
    print("\n  per strategy - mean return per trade: real | simulated with the delay | "
          "simulated in real time:")
    by_strategy = defaultdict(list)
    for t in trades:
        by_strategy[t["strategy"]].append(t)
    for name, rows in sorted(by_strategy.items(),
                             key=lambda kv: -statistics.fmean(t["real_ret"] for t in kv[1])):
        if len(rows) < 10:
            continue
        print(f"    {name:20s} n={len(rows):3d}  {statistics.fmean(t['real_ret'] for t in rows):+6.1f}% | "
              f"{statistics.fmean(t['sim'][QUOTE_LAG_MINUTES].ret_pct for t in rows):+6.1f}% | "
              f"{statistics.fmean(t['sim'][0].ret_pct for t in rows):+6.1f}%")
    print(f"\nLabel check: {'PASS' if verdict else 'FAIL'} "
          f"(needs >= 75% exit match and mean within 5 points, at the app's quote delay)")
    return verdict


def cmd_fetch(config: dict) -> None:
    db, cache = _paths(config)
    keys = history.load_keys(Path(db).parent)
    print(f"Fetching 1-minute bars into {cache}")
    for ticker in TICKERS:
        history.update_cache(cache, ticker, keys, date.today() - timedelta(days=1))
    history.update_vix(cache)


def _labels_and_signals(config: dict, cache: Path) -> tuple[dict, dict, dict]:
    calibration = _load_calibration(cache)
    vix = history.load_vix(cache)
    if not vix:
        raise SystemExit("No VIX history - run: python research.py fetch")
    exit_cfg = next(s["exit"] for s in config["shadow_strategies"] if s["name"] == "autopilot_point")
    sessions, labels, counts = {}, {}, defaultdict(int)
    for ticker in TICKERS:
        rows = history.load_bars(cache, ticker)
        if not rows:
            raise SystemExit(f"No {ticker} bars - run: python research.py fetch")
        sessions[ticker] = session_lab.sessions_from_bars(rows)
        k, half = calibration["k"][ticker], calibration["half_spread"][ticker]
        for session in sessions[ticker]:
            counts["sessions"] += 1
            if not session.complete():
                counts["incomplete or half-day"] += 1
                continue
            if session.day not in vix:
                counts["no VIX"] += 1
                continue
            label = session_lab.label_session(session, k * vix[session.day][0] / 100.0,
                                              exit_cfg, half_spread=half)
            if label is None:
                counts["unpriceable"] += 1
                continue
            labels[(ticker, session.day)] = label
    signals = session_lab.build_signals(sessions, {d: c for d, (_, c) in vix.items()})
    counts["labelled"] = len(labels)
    return labels, signals, dict(counts)


def _fmt(value, spec="+.1f", none="-"):
    return none if value is None else format(value, spec)


def _pct(value) -> str:
    return "-" if value is None else f"{value:+.1f}%"


def _table(results: list[dict]) -> list[str]:
    periods = [p for p, _, _ in session_lab.PERIODS]
    lines = ["| Signal | Side | Days | Excess/trade | t | " + " | ".join(periods)
             + " | Net since 2022 | Win % | Verdict |",
             "|---|---|---|---|---|" + "---|" * len(periods) + "---|---|---|"]
    for r in results:
        cells = [f"{_fmt(r['periods'][p]['mean'])} ({r['periods'][p]['days']})" for p in periods]
        lines.append(f"| {r['signal']} | {r['orientation']} | {r['days']} | "
                     f"{_pct(r['mean_excess'])} | {r['t']:+.2f} | " + " | ".join(cells)
                     + f" | {_pct(r['net_since_2022'])} | {_fmt(r['win_rate'], '.0f')} | "
                     f"**{r['verdict']}** |")
    return lines


def cmd_run(config: dict, report: str | None) -> None:
    _, cache = _paths(config)
    calibration = _load_calibration(cache)
    labels, signals, counts = _labels_and_signals(config, cache)
    floor = session_lab.noise_floor(len(session_lab.SIGNALS) * len(session_lab.ORIENTATIONS))
    results = session_lab.run_all(signals, labels)
    strong = session_lab.run_all(signals, labels, min_strength=0.5)
    nulls = session_lab.null_summary(labels)

    lines = [f"Sessions: {counts}", "",
             f"Calibration: k = {json.dumps(calibration['k'])}, half-spread = "
             f"{json.dumps(calibration['half_spread'])}", "",
             "Blind trade (return on premium per trade):", "",
             "| Period | Ticker-sessions | Coin flip | Always call | Always put |",
             "|---|---|---|---|---|"]
    for period, n in nulls.items():
        lines.append(f"| {period} | {n['sessions']} | {n['coin']:+.1f}% | {n['call']:+.1f}% | "
                     f"{n['put']:+.1f}% |")
    lines += ["", f"All 20 tests (noise floor t = {floor:.2f}); per-period cells are mean "
              f"excess % (days):", ""] + _table(results)
    lines += ["", "Descriptive only - same tests restricted to |score| >= 0.5:", ""] + _table(strong)
    text = "\n".join(lines)
    print(text)
    if report:
        Path(report).write_text(text + "\n", encoding="utf-8")
        print(f"\nWrote {report}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("calibrate")
    sub.add_parser("validate-label")
    sub.add_parser("fetch")
    run = sub.add_parser("run")
    run.add_argument("--report", default=None, help="also write the tables to this file")
    args = parser.parse_args()
    config = load_settings()
    if args.command == "calibrate":
        cmd_calibrate(config)
    elif args.command == "validate-label":
        cmd_validate(config)
    elif args.command == "fetch":
        cmd_fetch(config)
    else:
        cmd_run(config, args.report)


if __name__ == "__main__":
    main()
