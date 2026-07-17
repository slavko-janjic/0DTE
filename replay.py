"""Historical replay CLI: validates the bar-derived (technicals) signal leg and
the opening-range tactic against weeks of real 5-minute bars - thousands of
graded samples in one run instead of waiting weeks of live polling.

    python replay.py                    # all configured tickers, ~59 days
    python replay.py --tickers QQQ,SPY --days 30
    python replay.py --decision 60      # simulate the tactic decision at open+60min

Report-only: nothing is written to the database. Sentiment/Kalshi/IV/news have
no free history, so this validates the technicals leg - not the full composite.
"""
import argparse

from analytics import accuracy, replay, walkforward
from config import load_settings
from data import market_data


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "n/a"


def run_replay(ticker: str, days: int, horizon: int, decision: int,
               min_confidence: float, tz_name: str) -> None:
    print(f"\n=== {ticker} · last {days} trading-ish days · 5m bars ===")
    bars = market_data.get_intraday_bars(ticker, interval="5m", period=f"{days}d")
    if bars is None:
        print("  no bars available (yfinance) - skipping")
        return
    sessions = replay.bars_to_sessions(bars, tz_name)
    snapshots = replay.replay_technicals(sessions)
    if not snapshots:
        print("  not enough bars to replay - skipping")
        return

    evaluated = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=horizon)
    graded = sum(1 for s in evaluated if s["evaluated"])
    overall = accuracy.overall_accuracy_pct(evaluated)
    print(f"  sessions: {len(sessions)}   snapshots: {len(snapshots)}   graded: {graded}")
    print(f"  technicals direction accuracy @{horizon}min: {_fmt_pct(overall)} "
          f"(50% = coin flip)")

    print("  by predicted confidence:")
    for band in accuracy.confidence_calibration(evaluated):
        print(f"    {band['band']:>8}: {_fmt_pct(band['observed_accuracy_pct'])} "
              f"observed over {band['count']} calls")

    periods = replay.session_period_accuracy(evaluated, tz_name)
    parts = [f"{name} {_fmt_pct(stats['accuracy_pct'])} ({stats['graded']})"
             for name, stats in periods.items()]
    print(f"  by time of day: {' · '.join(parts)}")

    stats = replay.opening_range_tactic_stats(
        sessions, decision_minutes=decision, min_confidence_pct=min_confidence)
    if stats["trades"]:
        print(f"  opening-range tactic (decide at open+{decision}min, "
              f"min conf {min_confidence:.0f}%): {stats['trades']} trades over "
              f"{stats['sessions']} sessions, win rate {_fmt_pct(stats['win_rate_pct'])}, "
              f"avg favorable spot move {stats['avg_favorable_move_pct']:+.2f}%")
    else:
        print(f"  opening-range tactic: no qualifying trades over {stats['sessions']} sessions")


def _attach_forward_returns(snapshots: list, horizon: int) -> list:
    """Adds the realised forward return at `horizon` to each snapshot, which is
    what a rule's P&L is scored on (accuracy grades direction; this grades the
    move you'd actually have captured)."""
    from datetime import timedelta
    rows = []
    for i, s in enumerate(snapshots):
        target = s["timestamp"] + timedelta(minutes=horizon)
        future = next((l["spot_price"] for l in snapshots[i + 1:]
                       if l["timestamp"] >= target and l["spot_price"] is not None), None)
        if future is None:
            continue
        rows.append(dict(s, fwd=(future - s["spot_price"]) / s["spot_price"] * 100.0))
    return rows


def run_walkforward(ticker: str, days: int, horizon: int, tz_name: str,
                    train_frac: float) -> None:
    """Let a naive optimiser loose, then judge only on data it never saw."""
    print(f"\n=== {ticker} · walk-forward validation ===")
    bars = market_data.get_intraday_bars(ticker, interval="5m", period=f"{days}d")
    if bars is None:
        print("  no bars available (yfinance) - skipping")
        return
    sessions = replay.bars_to_sessions(bars, tz_name)
    rows = _attach_forward_returns(replay.replay_technicals(sessions), horizon)
    if len(rows) < 100:
        print(f"  only {len(rows)} usable rows - not enough to split")
        return

    rules = walkforward.build_rule_grid(
        ["technicals"], [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5])
    result = walkforward.walk_forward_search(
        rules, rows, train_frac=train_frac, horizon_minutes=horizon)
    if "error" in result:
        print(f"  {result['error']}")
        return

    best = result["best_rule"]
    tr, te = result["train"], result["test"]
    print(f"  searched {result['rules_tested']} rules over {result['train_rows']} train "
          f"/ {result['test_rows']} test rows")
    print(f"  best in-sample rule: {best['signal']} "
          f"{'>' if best['sign'] > 0 else '< -'}{best['threshold']} "
          f"(sign {best['sign']:+d})")
    # independent trades is the honest sample size: 5-min bars on a 30-min
    # horizon overlap ~6x, and t-stats must not be paid for oversampling
    print(f"    TRAIN  {tr['total_pct']:+8.2f}%  {tr['trades']:>5} trades "
          f"({tr['independent_trades']:>4} independent)  t={tr['t_stat']:+.2f}")
    print(f"    TEST   {te['total_pct']:+8.2f}%  {te['trades']:>5} trades "
          f"({te['independent_trades']:>4} independent)  t={te['t_stat']:+.2f}")
    print(f"  noise floor (best t expected from PURE NOISE after "
          f"{result['rules_tested']} searches): {result['noise_floor_t']:.2f}")
    print(f"  top-decile in-sample winners that stayed profitable out of sample: "
          f"{result['top_decile_survivors']}/{result['top_decile_n']} "
          f"(avg {result['top_decile_oos_mean_pct']:+.2f}%)")
    print(f"  --> {result['verdict']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--walkforward", action="store_true",
                        help="search rules on a train split and judge them on unseen data")
    parser.add_argument("--train-frac", type=float, default=0.7,
                        help="fraction of history used to fit (rest is held out)")
    parser.add_argument("--days", type=int, default=59,
                        help="calendar days of 5m bars to fetch (yfinance max ~60)")
    parser.add_argument("--tickers", type=str, default=None,
                        help="comma-separated; default = tickers from settings.yaml")
    parser.add_argument("--horizon", type=int, default=None,
                        help="grading horizon in minutes; default = accuracy_horizon_minutes")
    parser.add_argument("--decision", type=int, default=60,
                        help="tactic decision point, minutes after the open")
    parser.add_argument("--min-confidence", type=float, default=0.0,
                        help="tactic skips days below this technicals confidence")
    args = parser.parse_args()

    config = load_settings()
    tickers = (args.tickers.split(",") if args.tickers else config["tickers"])
    horizon = args.horizon or config.get("accuracy_horizon_minutes", 30)
    tz_name = config["market_hours"].get("timezone", "America/New_York")

    if args.walkforward:
        print(f"Walk-forward validation over ~{args.days}d of 5m bars "
              f"(horizon {horizon}min, train {args.train_frac:.0%}). No DB writes.")
        print("A rule is only believed if it works on data it never saw.")
        for ticker in tickers:
            run_walkforward(ticker.strip(), args.days, horizon, tz_name, args.train_frac)
        print("\nNote: technicals leg only (no free history for sentiment/Kalshi/IV/news), "
              "and spot-based - real option P&L also pays the spread.")
        return

    print(f"Replaying technicals over ~{args.days}d of 5m bars "
          f"(horizon {horizon}min). Report-only - no DB writes.")
    for ticker in tickers:
        run_replay(ticker.strip(), args.days, horizon, args.decision,
                   args.min_confidence, tz_name)
    print("\nNote: this grades the technicals leg only - sentiment/Kalshi/IV/news "
          "have no free history. Tactic stats are spot-direction based, not option P&L.")


if __name__ == "__main__":
    main()
