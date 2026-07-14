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

from analytics import accuracy, replay
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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

    print(f"Replaying technicals over ~{args.days}d of 5m bars "
          f"(horizon {horizon}min). Report-only - no DB writes.")
    for ticker in tickers:
        run_replay(ticker.strip(), args.days, horizon, args.decision,
                   args.min_confidence, tz_name)
    print("\nNote: this grades the technicals leg only - sentiment/Kalshi/IV/news "
          "have no free history. Tactic stats are spot-direction based, not option P&L.")


if __name__ == "__main__":
    main()
