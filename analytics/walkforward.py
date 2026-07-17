"""Walk-forward validation: make a rule prove itself on data it has never seen.

Why this module exists. Turned loose on 11 days of live history, a naive search
over 398 rule combinations produced `NVDA order_flow > +0.3` returning +141% -
on a signal independently measured at z=+0.56, i.e. pure noise. Out of sample it
returned -9%, and 0 of the top 20 in-sample winners stayed profitable (average
-39%). That is not bad luck; it is the guaranteed output of testing more
hypotheses than you have independent observations.

The arithmetic: searching K rules against N independent observations, the best
in-sample result under PURE NOISE grows like sqrt(2*ln(K)/N) standard errors.
Report that noise floor next to every result, and an "amazing" backtest stops
being persuasive - it becomes exactly what you'd expect from nothing.

So: split chronologically, fit on train, judge only on test, and state plainly
how much of the in-sample result the search itself manufactured.

Pure functions over plain dicts, like the rest of analytics/.
"""
import math
import statistics


def chronological_split(rows: list, train_frac: float = 0.7) -> tuple[list, list]:
    """Split oldest-first rows into (train, test) by TIME, never randomly.

    Random splits leak the future into the past: adjacent minutes are nearly
    identical, so a shuffled holdout is already memorised. Only a chronological
    cut answers the real question - would this rule have worked on the days that
    hadn't happened yet?
    """
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda r: r["timestamp"])
    cut = int(len(ordered) * train_frac)
    return ordered[:cut], ordered[cut:]


def evaluate_rule(rule: dict, rows: list, horizon_minutes: float = 30.0) -> dict:
    """Runs one rule over rows carrying `subscores`, a forward return `fwd`, and
    a `timestamp`.

    rule: {"signal": str, "threshold": float, "sign": +1|-1}. Go long when the
    (sign-adjusted) subscore exceeds +threshold, short when below -threshold,
    otherwise stand aside.

    The t-stat uses INDEPENDENT trades, not the raw count. Trades taken from
    5-minute bars but graded on a 30-minute forward window overlap ~6x: 2,680
    such trades are ~450 real observations, and using the raw count inflates
    every t-stat by ~sqrt(6). That is the same correlated-sample error that let
    the calibrator hand trump_news half a ticker's weight - it must not be
    repeated in the tool built to catch exactly this.
    """
    signal, threshold, sign = rule["signal"], rule["threshold"], rule["sign"]
    rets, stamps = [], []
    for row in rows:
        value = (row.get("subscores") or {}).get(signal)
        fwd = row.get("fwd")
        if value is None or fwd is None:
            continue
        value *= sign
        if value > threshold:
            rets.append(fwd); stamps.append(row.get("timestamp"))
        elif value < -threshold:
            rets.append(-fwd); stamps.append(row.get("timestamp"))

    n = len(rets)
    if n < 2:
        return {"total_pct": sum(rets), "mean_pct": None, "trades": n,
                "independent_trades": n, "t_stat": 0.0}

    independent = _non_overlapping_count(stamps, horizon_minutes)
    mean = statistics.fmean(rets)
    stdev = statistics.stdev(rets)
    t = mean / (stdev / math.sqrt(independent)) if stdev and independent > 1 else 0.0
    return {"total_pct": sum(rets), "mean_pct": mean, "trades": n,
            "independent_trades": independent, "t_stat": t}


def _non_overlapping_count(stamps: list, horizon_minutes: float) -> int:
    """Trades whose forward windows don't overlap - the honest sample size.
    Mirrors analytics.accuracy.non_overlapping_count."""
    from datetime import timedelta
    ordered = sorted(s for s in stamps if s is not None)
    if not ordered:
        return 0
    count, window_end = 0, None
    for ts in ordered:
        if window_end is None or ts >= window_end:
            count += 1
            window_end = ts + timedelta(minutes=horizon_minutes)
    return count


def noise_floor_t(num_rules_tested: int) -> float:
    """The best t-stat you'd EXPECT from pure noise after searching this many
    rules: ~sqrt(2*ln(K)). Any winner below this is indistinguishable from the
    search itself having got lucky. This is the number that makes a +141%
    backtest stop being impressive."""
    if num_rules_tested < 2:
        return 0.0
    return math.sqrt(2 * math.log(num_rules_tested))


def walk_forward_search(
    rules: list[dict], rows: list, train_frac: float = 0.7, min_trades: int = 30,
    horizon_minutes: float = 30.0,
) -> dict:
    """Fit on the train split, judge on the test split, report honestly.

    Returns the in-sample winner, its out-of-sample result, how the whole
    top-decile behaved out of sample (the real tell - if the search found
    something real, its winners keep working), and the noise floor for the
    number of rules searched.
    """
    train, test = chronological_split(rows, train_frac)
    if not train or not test:
        return {"error": "not enough data to split"}

    scored = []
    for rule in rules:
        result = evaluate_rule(rule, train, horizon_minutes)
        if result["trades"] >= min_trades:
            scored.append((result["total_pct"], rule, result))
    if not scored:
        return {"error": "no rule cleared min_trades on the train split"}
    scored.sort(key=lambda x: x[0], reverse=True)

    best_train_total, best_rule, best_train = scored[0]
    best_test = evaluate_rule(best_rule, test, horizon_minutes)

    top_n = max(1, len(scored) // 10)
    oos = [evaluate_rule(r, test, horizon_minutes)["total_pct"] for _, r, _ in scored[:top_n]]
    survivors = sum(1 for x in oos if x > 0)

    floor = noise_floor_t(len(scored))
    survived = (best_test["total_pct"] > 0 and abs(best_test["t_stat"]) >= 2
                and abs(best_train["t_stat"]) >= floor)

    return {
        "rules_tested": len(scored),
        "train_rows": len(train), "test_rows": len(test),
        "best_rule": best_rule,
        "train": best_train, "test": best_test,
        "noise_floor_t": floor,
        "top_decile_oos_mean_pct": statistics.fmean(oos) if oos else None,
        "top_decile_survivors": survivors, "top_decile_n": len(oos),
        "survived": survived,
        "verdict": (
            "SURVIVED out-of-sample - worth pre-registering and forward-testing"
            if survived else
            "REJECTED - in-sample only; the search manufactured this"
        ),
    }


def build_rule_grid(signals: list[str], thresholds: list[float]) -> list[dict]:
    """The rule space a naive optimiser would sweep. Kept explicit so the count
    (and therefore the noise floor) is always visible to the caller."""
    return [
        {"signal": s, "threshold": t, "sign": sign}
        for s in signals for t in thresholds for sign in (1, -1)
    ]
