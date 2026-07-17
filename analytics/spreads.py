"""The cost of transacting, analysed.

Everything else in this app tries to predict direction, which 11 days of graded
data says is a coin flip at every horizon we can measure. The bid/ask spread is
the opposite kind of quantity: a mechanical toll paid on every round trip,
knowable in advance, and following a structural intraday pattern (market makers
quote wide at the open while price discovery is happening, tighten mid-morning,
then widen into the close). That makes it predictable in the way direction isn't
- and avoiding a known cost is worth as much as forecasting an unknown move.

Pure functions on plain dicts, like the rest of analytics/.
"""
import statistics


def playability_ratio(
    typical_move_pct: float | None, spot: float | None, delta: float | None,
    premium: float | None, spread_pct: float | None,
) -> float | None:
    """How many times a typical underlying move covers the round-trip spread.

    A move of typical_move_pct on the underlying shifts the option's value by
    roughly delta * move (in dollars), which as a fraction of the premium is the
    leveraged move the trade actually captures. Divide by the spread to get the
    signal-to-cost ratio.

    Below ~1 the average move doesn't even pay the toll - no achievable
    directional edge rescues that contract. None if any input is missing.
    """
    if not all(v for v in (typical_move_pct, spot, delta, premium, spread_pct)):
        return None
    underlying_move = typical_move_pct / 100.0 * spot
    premium_move_pct = abs(delta) * underlying_move / premium * 100.0
    return premium_move_pct / spread_pct


def spread_by_minute_bucket(quote_rows: list, bucket_minutes: int = 30) -> list[dict]:
    """The intraday cost curve: median spread per bucket of minutes-since-open.

    Median, not mean - a single stale quote can produce an absurd spread and
    would drag a mean around. Buckets with no quotes are omitted.
    Returns [{bucket_start, bucket_label, median_spread_pct, samples}] in time order.
    """
    buckets: dict[int, list[float]] = {}
    for row in quote_rows:
        mso = row["minutes_since_open"]
        spread = row["spread_pct"]
        if mso is None or spread is None or mso < 0:
            continue
        key = int(mso // bucket_minutes) * bucket_minutes
        buckets.setdefault(key, []).append(spread)

    results = []
    for start in sorted(buckets):
        spreads = buckets[start]
        end = start + bucket_minutes
        results.append({
            "bucket_start": start,
            "bucket_label": f"{start}-{end}m",
            "median_spread_pct": statistics.median(spreads),
            "samples": len(spreads),
        })
    return results


def cheapest_windows(buckets: list[dict], top: int = 3, min_samples: int = 5) -> list[dict]:
    """The buckets where transacting costs least - i.e. when to actually trade.
    Buckets thinner than min_samples are excluded rather than trusted."""
    eligible = [b for b in buckets if b["samples"] >= min_samples]
    return sorted(eligible, key=lambda b: b["median_spread_pct"])[:top]


def spread_summary(quote_rows: list) -> dict:
    """Median / best / worst spread across the given quotes, plus sample count.
    Median is the honest 'typical' cost; the worst tells you how bad it gets."""
    spreads = [r["spread_pct"] for r in quote_rows if r["spread_pct"] is not None]
    if not spreads:
        return {"samples": 0, "median_pct": None, "best_pct": None, "worst_pct": None}
    return {
        "samples": len(spreads),
        "median_pct": statistics.median(spreads),
        "best_pct": min(spreads),
        "worst_pct": max(spreads),
    }
