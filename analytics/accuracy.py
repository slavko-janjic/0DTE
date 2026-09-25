"""Pure, testable functions for scoring how often past signals called the
right direction. Takes plain dicts with datetime timestamps rather than DB
rows, so it's easy to unit test with synthetic data.

A signal is "evaluated" once a later snapshot exists at least horizon_minutes
ahead with a known spot price - and no more than GRADE_MAX_LAG_MINUTES past
that mark. Until then there's nothing to grade it against; and a price from
much later (the next morning, after a weekend, across a worker outage) isn't
the price horizon_minutes later, so the call stays ungraded rather than being
scored on a move it never predicted. Neutral signals ("no clear edge") are
excluded since they made no directional call.
"""
import json
import math
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from signals.composite import direction_from_score

DEFAULT_WINDOW_DAYS = 30

# How late past the horizon a grading price may be and still count as "the
# price horizon_minutes later". Polling is every minute (5 in early history),
# so a normal grading price is well inside this; the overnight gap, a weekend
# or a worker outage are not.
GRADE_MAX_LAG_MINUTES = 10


# --- which history gets graded -----------------------------------------------
# Two different spans. Each signal's own score is stored per snapshot, so
# per-signal accuracy can use the whole look-back window. The COMPOSITE is only
# comparable with itself: when signals are added, removed or rescored it becomes
# a different signal, so composite-level stats (overall accuracy, confidence
# bands, the calibration confidence map) start at config's composite_since.

def history_window_start(config: dict, now: datetime | None = None) -> datetime:
    """Start of the graded look-back window (UTC): accuracy_window_days back.
    A time window, not a row count - a 2,000-row limit was ~5.5 sessions at
    one poll a minute, far too little to judge a signal on."""
    now = now if now is not None else datetime.now(timezone.utc)
    days = config.get("accuracy_window_days", DEFAULT_WINDOW_DAYS)
    return now.astimezone(timezone.utc) - timedelta(days=days)


def composite_start(config: dict) -> datetime | None:
    """Midnight (market tz) of config's composite_since date, or None when unset."""
    since = config.get("composite_since")
    if not since:
        return None
    tz = ZoneInfo(config.get("market_hours", {}).get("timezone", "America/New_York"))
    day = since if isinstance(since, date) else date.fromisoformat(str(since))
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def since_composite(history: list[dict], start: datetime | None) -> list[dict]:
    """The part of a history produced by the current composite definition."""
    if start is None:
        return history
    return [snap for snap in history if snap["timestamp"] >= start]


def evaluate_signal_accuracy(snapshots: list[dict], horizon_minutes: float,
                             max_lag_minutes: float | None = None) -> list[dict]:
    """snapshots: ascending by timestamp, each with timestamp/direction/spot_price.
    Returns a new list with 'evaluated', 'hit', and 'future_price' added to each dict.

    The grading price is the first LATER snapshot at or past the horizon, found
    by bisection - O(n log n), so a long look-back window stays cheap. (Slicing
    the tail for every snapshot copied the rest of the list each time -
    quadratic as the window grows.)

    It must also land within max_lag_minutes (default GRADE_MAX_LAG_MINUTES) of
    the horizon. The worker only polls during the session, so a call made in
    its last half hour used to be graded on the NEXT morning's price - the
    overnight gap, not the call (7.2% of graded QQQ/SPY calls). Those, and
    calls straddling a weekend or worker outage, now stay ungraded."""
    lag_minutes = GRADE_MAX_LAG_MINUTES if max_lag_minutes is None else max_lag_minutes
    # math.inf = no limit (the old behaviour); timedelta itself can't hold infinity
    max_lag = timedelta.max if math.isinf(lag_minutes) else timedelta(minutes=lag_minutes)
    times = [snap.get("timestamp") for snap in snapshots]
    results = []
    for i, snap in enumerate(snapshots):
        entry = dict(snap, evaluated=False, hit=None, future_price=None)
        direction, spot, ts = snap.get("direction"), snap.get("spot_price"), snap.get("timestamp")
        if direction == "neutral" or spot is None or ts is None:
            results.append(entry)
            continue

        target_time = ts + timedelta(minutes=horizon_minutes)
        j = bisect_left(times, target_time, lo=i + 1)
        while j < len(snapshots) and snapshots[j].get("spot_price") is None:
            j += 1
        future_price = None
        if j < len(snapshots) and snapshots[j]["timestamp"] - target_time <= max_lag:
            future_price = snapshots[j]["spot_price"]
        if future_price is None:
            results.append(entry)
            continue

        hit = (future_price > spot) if direction == "bullish" else (future_price < spot)
        entry.update(evaluated=True, hit=hit, future_price=future_price)
        results.append(entry)
    return results


# --- effective sample size ---------------------------------------------------
# We poll every minute but grade against a price 30 minutes out, so consecutive
# snapshots are NOT independent observations: their forward windows overlap ~29/30
# and their subscores are near-identical. Counting raw minutes as samples inflates
# every trust gate by ~30x (and far more for a slow signal). Two distinct sources
# of over-counting, so two counters - the honest sample size is the stricter one.
#
# Accuracy PERCENTAGES still use every sample (more data = better point estimate).
# Only the gates that decide "do we trust this yet?" use the counts below.

def non_overlapping_count(evaluated_snapshots: list[dict], horizon_minutes: float) -> int:
    """Graded observations whose forward windows don't overlap: a greedy
    oldest-first walk that takes a sample then skips everything within
    horizon_minutes of it. 390 contiguous 1-min snapshots at a 30-min horizon
    give 13, not 390."""
    graded = sorted(
        (s for s in evaluated_snapshots if s.get("evaluated") and s.get("timestamp")),
        key=lambda s: s["timestamp"],
    )
    count = 0
    window_end = None
    for snap in graded:
        if window_end is None or snap["timestamp"] >= window_end:
            count += 1
            window_end = snap["timestamp"] + timedelta(minutes=horizon_minutes)
    return count


def direction_run_count(evaluated_snapshots: list[dict]) -> int:
    """Maximal runs of the same direction among graded snapshots. A signal that
    said 'bullish' all day made ONE call, not 390 - this is what stops a slow,
    market-wide signal (trump_news changes once or twice a day) from looking
    like hundreds of confirmations. Fast-flipping signals have a large run
    count, so the window counter binds for them instead."""
    graded = sorted(
        (s for s in evaluated_snapshots if s.get("evaluated") and s.get("timestamp")),
        key=lambda s: s["timestamp"],
    )
    runs = 0
    previous = object()  # sentinel: never equal to a direction string
    for snap in graded:
        if snap.get("direction") != previous:
            runs += 1
            previous = snap.get("direction")
    return runs


def independent_observations(evaluated_snapshots: list[dict], horizon_minutes: float) -> int:
    """The honest sample size: the stricter of the two over-counting corrections."""
    return min(
        non_overlapping_count(evaluated_snapshots, horizon_minutes),
        direction_run_count(evaluated_snapshots),
    )


def daily_accuracy_summary(evaluated_snapshots: list[dict], tz_name: str = "America/New_York") -> list[dict]:
    """Groups evaluated signals by trading day (in tz_name) into hit-rate buckets."""
    tz = ZoneInfo(tz_name)
    by_day: dict = defaultdict(lambda: {"total": 0, "hits": 0})
    for snap in evaluated_snapshots:
        if not snap.get("evaluated"):
            continue
        bucket = by_day[snap["timestamp"].astimezone(tz).date()]
        bucket["total"] += 1
        bucket["hits"] += 1 if snap["hit"] else 0

    return [
        {
            "date": day.isoformat(),
            "total": bucket["total"],
            "hits": bucket["hits"],
            "accuracy_pct": bucket["hits"] / bucket["total"] * 100.0,
        }
        for day, bucket in sorted(by_day.items())
    ]


def overall_accuracy_pct(evaluated_snapshots: list[dict]) -> float | None:
    evaluated = [s for s in evaluated_snapshots if s.get("evaluated")]
    if not evaluated:
        return None
    return sum(1 for s in evaluated if s["hit"]) / len(evaluated) * 100.0


# --- context-conditional accuracy -------------------------------------------
# "Which signals should I trust given what kind of day it is?" The same graded
# calls, bucketed by market context (time of day, volatility regime, gap size)
# rather than averaged into one number. Read-only analysis for now - the
# hook the calibrator would eventually condition weights on.

def bucket_evaluated(evaluated_snapshots: list[dict], context_fn) -> dict[str, dict]:
    """Buckets an already-graded list (from evaluate_signal_accuracy) by a
    context label. context_fn maps an evaluated snapshot to a bucket string, or
    None to exclude it. Returns {label: {"graded": int, "accuracy_pct": float|None}}."""
    buckets: dict = defaultdict(lambda: [0, 0])  # [graded, hits]
    for snap in evaluated_snapshots:
        if not snap.get("evaluated"):
            continue
        label = context_fn(snap)
        if label is None:
            continue
        buckets[label][0] += 1
        buckets[label][1] += 1 if snap["hit"] else 0
    return {
        label: {"graded": total, "accuracy_pct": (hits / total * 100.0) if total else None}
        for label, (total, hits) in buckets.items()
    }


def accuracy_by_context(
    history: list[dict], horizon_minutes: float, context_fn, category: str | None = None,
) -> dict[str, dict]:
    """Per-context accuracy for the composite (category=None) or one signal
    category. Grades the relevant direction, then buckets by context_fn. Used
    for per-category context cuts and by tests; the dashboard reuses an
    already-graded list via bucket_evaluated for the composite case."""
    if category is None:
        series = history
    else:
        series = [
            dict(row, direction=(
                direction_from_score(row["subscores"][category])
                if category in row.get("subscores", {}) else "neutral"
            ))
            for row in history
        ]
    evaluated = evaluate_signal_accuracy(series, horizon_minutes)
    return bucket_evaluated(evaluated, context_fn)


def context_time_of_day(snap: dict, tz_name: str = "America/New_York") -> str:
    """Session period of a snapshot's call: morning (<11:00), midday (11:00-14:00),
    afternoon (>=14:00), market-local time. Replay showed the technicals leg is
    much better in the morning than the afternoon."""
    local = snap["timestamp"].astimezone(ZoneInfo(tz_name))
    hour = local.hour + local.minute / 60.0
    return "morning" if hour < 11 else ("midday" if hour < 14 else "afternoon")


def context_direction_streak(snap: dict) -> str | None:
    """How settled the call was when it was made: fresh (just flipped), building,
    or sustained. Answers whether persistence is worth anything - a 1-minute blip
    and a 40-minute conviction currently produce an identical confidence, so this
    is the first look at whether they deserve the same trust. None on snapshots
    predating streak tracking."""
    streak = snap.get("direction_streak")
    if streak is None:
        return None
    if streak <= 3:
        return "fresh (1-3)"
    if streak <= 15:
        return "building (4-15)"
    return "sustained (16+)"


def context_volatility_regime(snap: dict) -> str | None:
    """Calm vs stressed, from the volatility_regime subscore already stored on
    each snapshot (positive = contango/low-VVIX/calm, negative = backwardation/
    elevated-VVIX/stress). None when that signal had no data that cycle."""
    value = (snap.get("subscores") or {}).get("volatility_regime")
    if value is None:
        return None
    return "calm" if value >= 0 else "stressed"


def evaluate_category_accuracy(history: list[dict], horizon_minutes: float) -> dict[str, dict]:
    """Breaks the overall accuracy check down per signal category, so you can see
    which individual signals (technicals, IV skew, Kalshi, VIX regime, etc.) are
    actually calling direction correctly rather than just the composite score.

    history: ascending by timestamp, each with timestamp/spot_price/subscores
    (a dict of category -> raw -1..1 score, as stored in signal_snapshots).
    A category missing from a given snapshot's subscores is treated as neutral
    for that snapshot (excluded from grading), not as a wrong call.

    Returns {category: {"accuracy_pct", "graded_count", "independent_count"}}.
    graded_count is every graded minute (what the accuracy_pct is computed over);
    independent_count is the honest sample size the trust gates use - see
    independent_observations().
    """
    categories: set[str] = set()
    for row in history:
        categories.update(row.get("subscores", {}).keys())

    results = {}
    for category in categories:
        synthetic = [
            {
                "timestamp": row["timestamp"],
                "spot_price": row["spot_price"],
                "direction": (
                    direction_from_score(row["subscores"][category])
                    if category in row.get("subscores", {})
                    else "neutral"
                ),
            }
            for row in history
        ]
        evaluated = evaluate_signal_accuracy(synthetic, horizon_minutes)
        results[category] = {
            "accuracy_pct": overall_accuracy_pct(evaluated),
            "graded_count": sum(1 for s in evaluated if s["evaluated"]),
            "independent_count": independent_observations(evaluated, horizon_minutes),
        }
    return results


def confidence_calibration(
    evaluated_snapshots: list[dict],
    bands: tuple[tuple[float, float], ...] = ((0, 20), (20, 40), (40, 60), (60, 101)),
) -> list[dict]:
    """Buckets graded signals by their *predicted* confidence and reports the
    *observed* hit rate per bucket - answers "when the app says 60%, is it
    actually right 60% of the time?". Bands are [lo, hi) ranges in percent.
    Bands with no graded signals are omitted."""
    results = []
    for lo, hi in bands:
        in_band = [
            s for s in evaluated_snapshots
            if s.get("evaluated") and s.get("confidence") is not None and lo <= s["confidence"] < hi
        ]
        if not in_band:
            continue
        hits = sum(1 for s in in_band if s["hit"])
        results.append({
            "band": f"{lo:.0f}-{min(hi, 100):.0f}%",
            "count": len(in_band),
            "observed_accuracy_pct": hits / len(in_band) * 100.0,
        })
    return results


def _sample_size(result: dict) -> int:
    """The count a trust gate should use: the honest independent count when the
    caller supplied one, else the raw graded count (keeps older callers and any
    hand-built dicts working)."""
    return result.get("independent_count", result.get("graded_count", 0))


def inversion_candidates(
    category_accuracy: dict[str, dict], min_graded: int = 10, max_accuracy_pct: float = 40.0,
) -> list[str]:
    """Categories that are reliably *wrong* - enough INDEPENDENT samples (not raw
    correlated minutes) and a hit rate meaningfully below a coin flip. A
    consistently-wrong signal is still information: its inverse is consistently
    right. Informational only; the user opts in by listing a category under
    invert_categories in config/settings.yaml."""
    return sorted(
        cat for cat, result in category_accuracy.items()
        if _sample_size(result) >= min_graded
        and result["accuracy_pct"] is not None
        and result["accuracy_pct"] <= max_accuracy_pct
    )


def suggest_weights(
    category_accuracy: dict[str, dict], current_weights: dict[str, float], min_graded: int = 10,
) -> dict[str, float] | None:
    """Proposes rebalanced weights based on evaluate_category_accuracy() results -
    informational only, never applied automatically (the user edits config/settings.yaml
    themselves if they agree).

    Only categories with at least min_graded INDEPENDENT samples are considered
    "proven" enough to reweight; everything else keeps its current config weight
    untouched and is excluded from the reallocation, so an under-sampled category
    can't be over- or under-weighted on a fluke. Counting raw minutes here is what
    let a slow, market-wide signal (trump_news) collect half the composite's
    weight off a handful of real calls - see independent_observations(). Within
    the eligible set, weight is redistributed proportional to how far above a
    coin-flip (50%) each category's accuracy is - a small floor keeps every
    eligible category present rather than zeroing one out entirely on a single
    bad stretch.

    Returns None if no category has enough independent history yet.
    """
    eligible = {
        cat: result["accuracy_pct"]
        for cat, result in category_accuracy.items()
        if _sample_size(result) >= min_graded
    }
    if not eligible:
        return None

    raw_scores = {cat: max(acc - 50.0, 0.5) for cat, acc in eligible.items()}
    total_raw = sum(raw_scores.values())
    reallocatable_weight = sum(current_weights.get(cat, 0.0) for cat in eligible)

    suggested = dict(current_weights)
    for cat, raw in raw_scores.items():
        suggested[cat] = round((raw / total_raw) * reallocatable_weight, 3)
    return suggested


# --- automatic self-calibration ---------------------------------------------
# Pure decision logic for the worker's daily calibration pass. The worker
# executes the returned actions (DB writes + audit log); nothing here does I/O.

_DEFAULT_BANDS: tuple[tuple[float, float], ...] = ((0, 20), (20, 40), (40, 60), (60, 101))


def blend_weights(current: dict[str, float], suggested: dict[str, float], rate: float) -> dict[str, float]:
    """Moves each weight a fraction of the way from current toward suggested -
    a gradual daily nudge instead of a full jump, so one anomalous day can't
    swing the composite. Categories absent from suggested keep their current
    weight. rate=0 -> current unchanged; rate=1 -> suggested exactly."""
    return {
        cat: round(weight + rate * (suggested.get(cat, weight) - weight), 4)
        for cat, weight in current.items()
    }


def confidence_map(
    evaluated_snapshots: list[dict],
    horizon_minutes: float,
    bands: tuple[tuple[float, float], ...] = _DEFAULT_BANDS,
) -> list[dict]:
    """The storable per-ticker calibration map: confidence_calibration() plus
    the numeric band bounds, so calibrated_confidence() can look up which band
    a raw confidence falls into without parsing labels.

    Each band carries both counts: `count` (every graded minute, what the
    observed accuracy is computed over) and `independent_count` (non-overlapping
    windows - the honest sample size calibrated_confidence() gates on). Bands
    mix directions, so run-counting doesn't apply here; only the window
    correction does."""
    results = []
    for lo, hi in bands:
        in_band = [
            s for s in evaluated_snapshots
            if s.get("evaluated") and s.get("confidence") is not None and lo <= s["confidence"] < hi
        ]
        if not in_band:
            continue
        hits = sum(1 for s in in_band if s["hit"])
        results.append({
            "lo": lo,
            "hi": hi,
            "count": len(in_band),
            "independent_count": non_overlapping_count(in_band, horizon_minutes),
            "observed_accuracy_pct": round(hits / len(in_band) * 100.0, 1),
        })
    return results


def calibrated_confidence(
    raw_pct: float, bands: list[dict] | None, min_band_count: int = 5,
) -> float:
    """Maps a raw confidence to the historically observed accuracy of its band
    (from a stored confidence_map). Falls back to the raw value when there is
    no map, the raw value falls outside every stored band, or the matching band
    has too few INDEPENDENT samples to trust. Clamped to 0-100.

    min_band_count is compared against the band's independent_count; maps stored
    before that field existed fall back to the raw `count` and self-heal on the
    next nightly calibration pass."""
    band = _trusted_band(raw_pct, bands, min_band_count)
    if band is None:
        return raw_pct
    return max(0.0, min(100.0, band["observed_accuracy_pct"]))


# One-sided 90%: the gate only trusts a hit rate the sample can back up.
DEFAULT_GATE_Z = 1.645


def gate_confidence(
    raw_pct: float, bands: list[dict] | None, min_band_count: int = 5,
    z: float = DEFAULT_GATE_Z,
) -> float:
    """The confidence the autopilot GATES on: the lower confidence bound of
    the matched band's observed hit rate, not its point estimate.

    calibrated_confidence() is the right number to display, but gating on it
    compared a thin-sample point estimate against a threshold set on the raw
    scale: a QQQ band right 56% of the time over 41 independent calls let a
    raw 6% signal clear the 55% gate, though its 90% lower bound is ~43%.
    Falls back to raw exactly like calibrated_confidence (no trusted band
    means the raw scale the gate was designed for). z=0 gives back the point
    estimate - the old rule."""
    band = _trusted_band(raw_pct, bands, min_band_count)
    if band is None:
        return raw_pct
    n = band.get("independent_count", band.get("count", 0))
    return wilson_lower_bound(band["observed_accuracy_pct"], n, z)


def wilson_lower_bound(accuracy_pct: float, n: int, z: float = DEFAULT_GATE_Z) -> float:
    """Wilson score lower bound (percent) for a hit rate seen over n trials -
    well-behaved for small n and rates near 0/100, unlike p - z*SE."""
    if n <= 0:
        return 0.0
    p = max(0.0, min(1.0, accuracy_pct / 100.0))
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, min(100.0, (centre - margin) / denominator * 100.0))


def _trusted_band(raw_pct: float, bands: list[dict] | None, min_band_count: int) -> dict | None:
    """The stored band a raw confidence falls in, if it has enough INDEPENDENT
    samples to be trusted; else None."""
    for band in bands or []:
        if band["lo"] <= raw_pct < band["hi"]:
            sample_size = band.get("independent_count", band.get("count", 0))
            return band if sample_size >= min_band_count else None
    return None


def history_snapshots(rows) -> list[dict]:
    """Converts signal_snapshots DB rows (oldest-first) into the plain dicts
    the evaluate_* functions grade - shared by the worker's calibration pass
    and the dashboard's accuracy panel."""
    return [
        {
            "timestamp": datetime.fromisoformat(row["timestamp"]),
            "direction": row["direction"],
            "spot_price": row["spot_price"],
            "confidence": row["confidence"],
            "composite_score": row["composite_score"],
            "subscores": json.loads(row["subscores_json"]),
            "direction_streak": _row_get(row, "direction_streak"),
        }
        for row in rows
    ]


def _row_get(row, column: str):
    """sqlite3.Row has no .get(), and older rows predate newer columns."""
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


def _categories_on_cooldown(
    recent_events: list[dict], today: date, cooldown_days: int,
) -> set[str]:
    """Categories with an inversion flip (either direction) within the cooldown
    window - blocked from flipping again so calibration can't oscillate daily."""
    on_cooldown = set()
    for event in recent_events:
        if event.get("kind") not in ("inversion_added", "inversion_removed"):
            continue
        category = (event.get("detail") or {}).get("category")
        if not category:
            continue
        event_date = datetime.fromisoformat(event["created_at"]).date()
        if (today - event_date).days < cooldown_days:
            on_cooldown.add(category)
    return on_cooldown


def plan_calibration(
    ticker: str,
    history: list[dict],
    current_weights: dict[str, float],
    current_inversions: list[str],
    recent_events: list[dict],
    cfg: dict,
    today: date,
) -> list[dict]:
    """The whole daily calibration decision for one ticker, as data.

    history: snapshot dicts (see history_snapshots), graded against
    cfg["horizon_minutes"]. recent_events: prior calibration events for this
    ticker as {"kind", "detail": dict, "created_at": iso} - used for the
    inversion cooldown. Returns an ordered list of actions the worker applies:
      {"kind": "weights", "old": {...}, "new": {...}}
      {"kind": "invert", "category": str, "accuracy_pct": float}
      {"kind": "uninvert", "category": str, "accuracy_pct": float}
      {"kind": "confidence_map", "bands": [...]}

    Guard rails: every lever has a minimum graded-sample gate, and inversions
    have a per-category cooldown. Known limitation (v1): after an inversion the
    graded history mixes pre- and post-flip samples until the horizon window
    rolls past, which the cooldown papers over.

    Only categories in current_weights (the live signals) are graded: a removed
    signal still in the history would otherwise take a share of the suggested
    weight. cfg["composite_since"] (datetime or None) limits the confidence map
    to the current composite definition.
    """
    horizon = cfg.get("horizon_minutes", 30)
    actions: list[dict] = []

    category_accuracy = {
        category: result
        for category, result in evaluate_category_accuracy(history, horizon).items()
        if category in current_weights
    }

    # 1. weights: nudge a fraction of the way toward the accuracy-based suggestion
    suggested = suggest_weights(
        category_accuracy, current_weights, min_graded=cfg.get("weight_min_graded", 10),
    )
    if suggested is not None:
        new_weights = blend_weights(current_weights, suggested, cfg.get("learning_rate", 0.25))
        if new_weights != {cat: round(w, 4) for cat, w in current_weights.items()}:
            actions.append({"kind": "weights", "old": dict(current_weights), "new": new_weights})

    # 2. inversions: flip reliably-wrong categories, un-flip failed inversions
    min_graded = cfg.get("inversion_min_graded", 10)
    max_acc = cfg.get("inversion_max_accuracy_pct", 40.0)
    on_cooldown = _categories_on_cooldown(
        recent_events, today, cfg.get("inversion_cooldown_days", 5),
    )
    for category in inversion_candidates(category_accuracy, min_graded, max_acc):
        if category not in current_inversions and category not in on_cooldown:
            actions.append({
                "kind": "invert", "category": category,
                "accuracy_pct": category_accuracy[category]["accuracy_pct"],
            })
    for category in current_inversions:
        result = category_accuracy.get(category)
        # as-used accuracy still reliably wrong -> the inversion didn't help, undo it
        if (result is not None and category not in on_cooldown
                and _sample_size(result) >= min_graded
                and result["accuracy_pct"] is not None
                and result["accuracy_pct"] <= max_acc):
            actions.append({
                "kind": "uninvert", "category": category,
                "accuracy_pct": result["accuracy_pct"],
            })

    # 3. confidence map: enough INDEPENDENT composite calls -> store observed
    # accuracy per band (raw minute counts would clear any gate trivially)
    evaluated = evaluate_signal_accuracy(
        since_composite(history, cfg.get("composite_since")), horizon)
    if independent_observations(evaluated, horizon) >= cfg.get("confidence_min_graded", 20):
        bands = confidence_map(evaluated, horizon)
        if bands:
            actions.append({"kind": "confidence_map", "bands": bands})

    return actions
