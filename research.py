"""Signal research lab CLI - does a signal improve the autopilot's 10:00 trade?

    python research.py calibrate          # option-price model, from the app's own records
    python research.py validate-label     # does the simulated trade match real shadow trades?
    python research.py fetch              # download / update SPY+QQQ minute bars (Alpaca keys)
    python research.py run [--report SIGNAL_RESEARCH.md]
    python research.py intraday [--report PATH]   # later decision times + weekday/event slices
    python research.py oos [--since DATE] [--no-fetch] [--push]   # the passed signals on new data only

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

from analytics import intraday_lab, option_sim, session_lab
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


def _read_dates(path: Path) -> set:
    import csv
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        return {date.fromisoformat(row["date"]) for row in csv.DictReader(fh) if row.get("date")}


def _load_events(cache: Path) -> tuple[set, set, set, list, dict]:
    """FOMC / CPI / jobs dates and mega-cap earnings from events_raw/, plus the
    span each source covers (a slice is only judged inside its span)."""
    import csv
    raw = cache / "events_raw"
    fomc, cpi, jobs = (_read_dates(raw / f"{n}.csv") for n in ("fomc", "cpi", "jobs"))
    earnings, first_by_ticker, last = [], {}, None
    path = raw / "earnings.csv"
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not row.get("date"):
                    continue
                day = date.fromisoformat(row["date"])
                earnings.append((day, (row.get("timing") or "amc").strip().lower()))
                ticker = row.get("ticker", "?")
                first_by_ticker[ticker] = min(first_by_ticker.get(ticker, day), day)
                last = max(last or day, day)
    covered = {}
    for name, days in (("fomc", fomc), ("cpi", cpi), ("jobs", jobs)):
        if days:
            covered[name] = (min(days), max(days))
    if earnings:
        # only where all six names are covered - a missing ticker would read as "no event"
        covered["after_megacap_earnings"] = (max(first_by_ticker.values()), last)
    return fomc, cpi, jobs, earnings, covered


def _slice_table(rows: list[dict]) -> list[str]:
    lines = ["| Slice | Days | Excess in slice | Elsewhere | t (difference) | Verdict |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['slice']} | {r['days']} | {_pct(r['mean_in'])} | {_pct(r['mean_out'])} | "
                     f"{r['t_diff']:+.2f} | {r['verdict']} |")
    return lines


def cmd_intraday(config: dict, report: str | None) -> None:
    _, cache = _paths(config)
    calibration = _load_calibration(cache)
    vix = history.load_vix(cache)
    exit_cfg = next(s["exit"] for s in config["shadow_strategies"] if s["name"] == "autopilot_point")
    sessions = {t: session_lab.sessions_from_bars(history.load_bars(cache, t)) for t in TICKERS}

    def sigma_for(ticker, day):
        return calibration["k"][ticker] * vix[day][0] / 100.0 if day in vix else None

    signals_by_time, labels_by_time = {}, {}
    for minute in intraday_lab.DECISIONS:
        signals_by_time[minute] = intraday_lab.build_signals_at(sessions, minute)
        labels_by_time[minute] = intraday_lab.build_labels_at(
            sessions, minute, sigma_for, exit_cfg, calibration["half_spread"])
    results = intraday_lab.run_main_tests(signals_by_time, labels_by_time)

    fomc, cpi, jobs, earnings, covered = _load_events(cache)
    trading_days = sorted({s.day for ss in sessions.values() for s in ss})
    tags = intraday_lab.event_tags(trading_days, fomc, cpi, jobs, earnings)

    # the 10:00 edge from the first study, for its slices
    labels_10 = {}
    for ticker, ss in sessions.items():
        for s in ss:
            sigma = sigma_for(ticker, s.day)
            if s.complete() and sigma:
                label = session_lab.label_session(s, sigma, exit_cfg,
                                                  calibration["half_spread"][ticker])
                if label:
                    labels_10[(ticker, s.day)] = label
    signals_10 = session_lab.build_signals(sessions, {d: c for d, (_, c) in vix.items()})

    floor = session_lab.noise_floor(intraday_lab.MAIN_TESTS)
    lines = [f"Event coverage: " + ", ".join(f"{k} {v[0]}..{v[1]}" for k, v in covered.items())
             + f"; tagged days: " + ", ".join(
                 f"{t} {sum(1 for d in tags.values() if t in d)}" for t in intraday_lab.EVENT_TAGS), "",
             f"### Main tests: 32 (noise floor t = {floor:.2f})", "",
             "| Time | " + _table(results)[0][2:]]
    lines.append("|---|" + _table(results)[1][1:])
    for r, row in zip(results, _table(results)[2:]):
        lines.append(f"| {r['time']} " + row)

    lines += ["", "### Slices", ""]
    to_slice = [("gap_behaviour", 1, "10:00", signals_10, labels_10)]
    to_slice += [(r["signal"], 1 if r["orientation"] == "follow" else -1, r["time"],
                  signals_by_time[r["minute"]], labels_by_time[r["minute"]])
                 for r in results if r["verdict"] == "PASS"]
    for name, sign, clock, sigs, labs in to_slice:
        side = "follow" if sign > 0 else "fade"
        lines += [f"**{name} ({side}) at {clock}** — slice noise floor t = "
                  f"{session_lab.noise_floor(len(intraday_lab.SLICES)):.2f}", ""]
        lines += _slice_table(intraday_lab.slice_report(sigs, labs, name, sign, tags, covered))
        lines.append("")

    lines += ["### Blind trade by slice (descriptive only — net level not trustworthy)", "",
              "| Slice | " + " | ".join(intraday_lab.DECISIONS.values()) + " |",
              "|---|" + "---|" * len(intraday_lab.DECISIONS)]
    profiles = {m: intraday_lab.blind_profile(labels_by_time[m], tags) for m in intraday_lab.DECISIONS}
    for slice_name in ("all",) + intraday_lab.SLICES:
        cells = [f"{profiles[m][slice_name]['coin']:+.1f}% ({profiles[m][slice_name]['n']})"
                 if slice_name in profiles[m] else "-" for m in intraday_lab.DECISIONS]
        lines.append(f"| {slice_name} | " + " | ".join(cells) + " |")

    text = "\n".join(lines)
    print(text)
    if report:
        Path(report).write_text(text + "\n", encoding="utf-8")
        print(f"\nWrote {report}")


# What passed before 2026-10-20 and is now forward-tested. Out-of-sample means
# only sessions after the pre-registration date - nothing here gets re-fitted.
OOS_TESTS = (("gap_behaviour", 30, "10:00"), ("orb15", 30, "10:00"),
             ("trail30", 60, "10:30"), ("orb15", 60, "10:30"))
OOS_SINCE = "2026-10-21"


def oos_summary(results: list[dict], since: str) -> str:
    """One push-sized line per signal."""
    parts = []
    for r in results:
        if r["excess"] is None:
            parts.append(f"{r['name']} {r['time']}: {r['days']}d, too early")
        else:
            parts.append(f"{r['name']} {r['time']}: {r['excess']:+.1f}%/day over {r['days']}d "
                         f"(t {r['t']:+.1f}) - {r['read']}")
    return f"Since {since}: " + "; ".join(parts)


def _push_summary(config: dict, body: str) -> None:
    """Sends the summary to every device with notifications on (same keys and
    subscriptions as the app's alerts). Quietly skipped when there are none."""
    from webapi import push
    db, _ = _paths(config)
    if not push.AVAILABLE or not push.subscriptions(db):
        print("push: no devices subscribed - skipped")
        return
    result = push.send(db, push.load_or_create_key(db), None, {
        "title": "Monthly re-test: morning signals", "body": body,
        "tag": "oos", "url": "/#lab"})
    print(f"push: {result}")


def cmd_oos(config: dict, since: str, fetch: bool, push_result: bool = False) -> None:
    """The pre-registered signals (follow) on sessions from `since` on: the
    same label and excess as the research, no re-fitting. History before
    `since` is used only for the 20-session medians."""
    db, cache = _paths(config)
    if fetch:
        keys = history.load_keys(Path(db).parent)
        for ticker in TICKERS:
            history.update_cache(cache, ticker, keys, date.today() - timedelta(days=1),
                                 first_year=date.today().year)
        history.update_vix(cache)
    calibration = _load_calibration(cache)
    vix = history.load_vix(cache)
    exit_cfg = next(s["exit"] for s in config["shadow_strategies"] if s["name"] == "autopilot_point")
    sessions = {t: session_lab.sessions_from_bars(history.load_bars(cache, t)) for t in TICKERS}
    start = date.fromisoformat(since)
    # only recent sessions need labels; signals need the full history for their medians
    recent = {t: [s for s in ss if s.day >= start] for t, ss in sessions.items()}

    def sigma_for(ticker, day):
        return calibration["k"][ticker] * vix[day][0] / 100.0 if day in vix else None

    signals = {30: session_lab.build_signals(sessions, {d: c for d, (_, c) in vix.items()}),
               60: intraday_lab.build_signals_at(sessions, 60)}
    labels = {m: intraday_lab.build_labels_at(recent, m, sigma_for, exit_cfg,
                                              calibration["half_spread"]) for m in (30, 60)}
    last = max((s.day for ss in recent.values() for s in ss), default=None)
    print(f"Out-of-sample since {since} (data through {last}); follow side; excess = return "
          f"minus the coin flip, % of premium, one observation per day.\n")
    print("| Signal | Time | Days | Excess/day | t | Net/day | Read |")
    print("|---|---|---|---|---|---|---|")
    results = []
    for name, minute, clock in OOS_TESTS:
        days = session_lab.daily_outcomes(signals[minute], labels[minute], name, 1)
        excess = [v["excess"] for v in days.values()]
        net = [v["net"] for v in days.values()]
        if len(excess) < 3:
            print(f"| {name} | {clock} | {len(excess)} | - | - | - | too early |")
            results.append({"name": name, "time": clock, "days": len(excess), "excess": None})
            continue
        t = session_lab._t(excess)
        read = ("too early (< 20 days)" if len(excess) < 20 else
                "holding up" if statistics.fmean(excess) > 0 else "not holding up")
        print(f"| {name} | {clock} | {len(excess)} | {_pct(statistics.fmean(excess))} | {t:+.2f} | "
              f"{_pct(statistics.fmean(net))} | {read} |")
        results.append({"name": name, "time": clock, "days": len(excess),
                        "excess": statistics.fmean(excess), "t": t, "read": read})
    summary = oos_summary(results, since)
    print("\n" + summary)
    if push_result:
        _push_summary(config, summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("calibrate")
    sub.add_parser("validate-label")
    sub.add_parser("fetch")
    run = sub.add_parser("run")
    run.add_argument("--report", default=None, help="also write the tables to this file")
    intraday = sub.add_parser("intraday", help="later decision times + weekday/event slices")
    intraday.add_argument("--report", default=None, help="also write the tables to this file")
    oos = sub.add_parser("oos", help="the passed signals on new data only")
    oos.add_argument("--since", default=OOS_SINCE, help="first session to score (YYYY-MM-DD)")
    oos.add_argument("--no-fetch", action="store_true", help="use the cached bars as they are")
    oos.add_argument("--push", action="store_true", help="also push the summary to subscribed devices")
    args = parser.parse_args()
    config = load_settings()
    if args.command == "calibrate":
        cmd_calibrate(config)
    elif args.command == "validate-label":
        cmd_validate(config)
    elif args.command == "fetch":
        cmd_fetch(config)
    elif args.command == "intraday":
        cmd_intraday(config, args.report)
    elif args.command == "oos":
        cmd_oos(config, args.since, not args.no_fetch, args.push)
    else:
        cmd_run(config, args.report)


if __name__ == "__main__":
    main()
