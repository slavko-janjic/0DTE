"""Shadow strategy lab: named virtual strategies that trade a paper-within-paper
book alongside the real account. Each strategy = an entry rule (this module) +
an exit rule set (reusing engine.evaluate_exit via a Position built from the
shadow row + the strategy's exit config). Zero balance impact - the point is to
build N win/loss track records per market day instead of one, so strategy ideas
can be compared on evidence before any of them drives the real auto-pilot.

Pure functions only - the worker does the I/O.

Honest scope: shadow books are per-ticker (a strategy may hold one open shadow
position per ticker and its entry cap applies per ticker), so this tests
entry/exit RULES, not portfolio selection. All strategies see the same delayed
data and the same honest bid/ask fill model as the real paper account.
"""
import math
import statistics

from paper_trading.models import Position
from signals.composite import direction_from_score


def should_shadow_enter(
    entry_cfg: dict,
    direction: str,
    confidence_pct: float,
    minutes_since_open: float,
    minutes_to_close: float,
    gamma_regime: str | None,
    subscores: dict,
    has_open_for_ticker: bool,
    entries_today: int,
    direction_streak: int = 1,
) -> str | None:
    """Entry decision for one strategy on one ticker: 'call'/'put' or None.

    entry_cfg keys (all optional except min_confidence_pct):
      min_confidence_pct        - confidence floor
      window_start_minutes /    - entry window, minutes after the open
      window_end_minutes          (omit both = whole session)
      no_entry_last_minutes     - runway floor before the close (default 45)
      max_entries_per_day       - per ticker (default 1)
      gamma_block               - list of gamma regimes to stand down in
      require_agree             - a category whose subscore sign must match the
                                  traded direction (confluence); missing
                                  subscore blocks the entry
      invert                    - trade the OPPOSITE of the composite's call.
                                  Tests the contrarian hypothesis: if the graded
                                  history says a confidence band is reliably
                                  wrong, its inverse is reliably right. Applied
                                  first, so every check below sees the direction
                                  actually being traded.
      min_direction_streak /    - how many consecutive polls the direction must
      max_direction_streak        have held. The composite is memoryless, so a
                                  1-minute blip and a 40-minute conviction read
                                  identically; these let a strategy demand a
                                  settled signal (min) or a fresh flip (max).
    """
    if entry_cfg.get("invert"):
        direction = {"bullish": "bearish", "bearish": "bullish"}.get(direction, direction)

    if direction == "bullish":
        option_type = "call"
    elif direction == "bearish":
        option_type = "put"
    else:
        return None

    if confidence_pct < entry_cfg.get("min_confidence_pct", 55):
        return None

    start = entry_cfg.get("window_start_minutes")
    end = entry_cfg.get("window_end_minutes")
    if start is not None and minutes_since_open < start:
        return None
    if end is not None and minutes_since_open > end:
        return None
    if minutes_to_close < entry_cfg.get("no_entry_last_minutes", 45):
        return None

    if gamma_regime is not None and gamma_regime in entry_cfg.get("gamma_block", []):
        return None

    min_streak = entry_cfg.get("min_direction_streak")
    max_streak = entry_cfg.get("max_direction_streak")
    if min_streak is not None and direction_streak < min_streak:
        return None
    if max_streak is not None and direction_streak > max_streak:
        return None

    agree_category = entry_cfg.get("require_agree")
    if agree_category is not None:
        subscore = subscores.get(agree_category)
        if subscore is None or direction_from_score(subscore) != direction:
            return None

    if has_open_for_ticker:
        return None
    if entries_today >= entry_cfg.get("max_entries_per_day", 1):
        return None

    return option_type


def shadow_position_from_row(row, exit_cfg: dict) -> Position:
    """Builds an engine Position from a shadow_positions row so evaluate_exit
    applies unchanged. Per-trade stop/target come from the strategy's exit
    config (not stored per row - the strategy IS the config)."""
    return Position(
        id=row["id"], ticker=row["ticker"], option_type=row["option_type"],
        strike=row["strike"], expiration=row["expiration"], contracts=row["contracts"],
        entry_price=row["entry_price"], cost_basis=row["entry_price"] * row["contracts"] * 100,
        entry_composite_score=0.0, status=row["status"],
        current_price=row["current_price"], max_price=row["max_price"],
        profit_target_pct=exit_cfg.get("profit_target_pct"),
        stop_loss_pct=exit_cfg.get("stop_loss_pct"),
    )


def strategy_edge(closed_rows: list, min_trades: int = 20) -> dict[str, dict]:
    """Has a strategy earned the right to be believed?

    The null is NOT "a 50% win rate" - asymmetric exits (a +50% target vs a -35%
    stop) move the natural win rate away from 50% with zero skill involved. The
    honest question is whether average P&L per trade is distinguishable from
    zero: t = mean / (stdev / sqrt(n)). |t| >= 2 is the usual bar.

    Also reports trades_needed: how many trades it would take to prove an effect
    of the size currently observed. A strategy showing a big mean with a huge
    spread may need hundreds; that number is the honest answer to "when will we
    know?".

    Caveat the caller should surface: with ~10 strategies running, one will clear
    |t|>=2 by luck roughly 1 time in 2. Treat a single winner as a hypothesis to
    re-test, not a result.
    """
    by_strategy: dict[str, list] = {}
    for row in closed_rows:
        by_strategy.setdefault(row["strategy"], []).append(row["pnl"] or 0.0)

    results = {}
    for name, pnls in by_strategy.items():
        n = len(pnls)
        mean = statistics.fmean(pnls) if n else 0.0
        stdev = statistics.stdev(pnls) if n > 1 else 0.0
        std_err = stdev / math.sqrt(n) if n > 1 and stdev > 0 else 0.0
        t_stat = mean / std_err if std_err else 0.0

        trades_needed = None
        if stdev > 0 and mean != 0:
            trades_needed = math.ceil((1.96 * stdev / abs(mean)) ** 2)

        if n < min_trades:
            verdict = f"warming up ({n}/{min_trades})"
        elif t_stat >= 2:
            verdict = "EDGE (+)"
        elif t_stat <= -2:
            verdict = "EDGE (-)"
        else:
            verdict = "no edge"

        results[name] = {
            "trades": n, "mean_pnl": mean, "stdev": stdev,
            "t_stat": t_stat, "trades_needed": trades_needed, "verdict": verdict,
        }
    return results


def strategy_scorecard(closed_rows: list) -> dict[str, dict]:
    """Per-strategy performance from closed shadow rows: trade count, win rate,
    total/avg P&L, profit factor (gross wins / gross losses), and max drawdown
    on the cumulative P&L path (rows are walked oldest-first)."""
    by_strategy: dict[str, list] = {}
    for row in closed_rows:
        by_strategy.setdefault(row["strategy"], []).append(row)

    cards = {}
    for strategy, rows in by_strategy.items():
        rows = sorted(rows, key=lambda r: r["exit_time"] or "")
        pnls = [row["pnl"] or 0.0 for row in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win, gross_loss = sum(wins), -sum(losses)

        cumulative = peak = max_drawdown = 0.0
        for p in pnls:
            cumulative += p
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)

        cards[strategy] = {
            "trades": len(pnls),
            "win_rate_pct": (len(wins) / len(pnls) * 100.0) if pnls else None,
            "total_pnl": sum(pnls),
            "avg_pnl": (sum(pnls) / len(pnls)) if pnls else None,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
            "max_drawdown": max_drawdown,
        }
    return cards
