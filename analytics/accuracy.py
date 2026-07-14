"""Pure, testable functions for scoring how often past signals called the
right direction. Takes plain dicts with datetime timestamps rather than DB
rows, so it's easy to unit test with synthetic data.

A signal is "evaluated" once a later snapshot exists at least horizon_minutes
ahead with a known spot price - until then there's nothing to grade it against.
Neutral signals ("no clear edge") are excluded since they made no directional call.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from signals.composite import direction_from_score


def evaluate_signal_accuracy(snapshots: list[dict], horizon_minutes: float) -> list[dict]:
    """snapshots: ascending by timestamp, each with timestamp/direction/spot_price.
    Returns a new list with 'evaluated', 'hit', and 'future_price' added to each dict."""
    results = []
    for i, snap in enumerate(snapshots):
        entry = dict(snap, evaluated=False, hit=None, future_price=None)
        direction, spot, ts = snap.get("direction"), snap.get("spot_price"), snap.get("timestamp")
        if direction == "neutral" or spot is None or ts is None:
            results.append(entry)
            continue

        target_time = ts + timedelta(minutes=horizon_minutes)
        future_price = next(
            (later["spot_price"] for later in snapshots[i + 1:]
             if later["timestamp"] >= target_time and later.get("spot_price") is not None),
            None,
        )
        if future_price is None:
            results.append(entry)
            continue

        hit = (future_price > spot) if direction == "bullish" else (future_price < spot)
        entry.update(evaluated=True, hit=hit, future_price=future_price)
        results.append(entry)
    return results


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


def evaluate_category_accuracy(history: list[dict], horizon_minutes: float) -> dict[str, dict]:
    """Breaks the overall accuracy check down per signal category, so you can see
    which individual signals (technicals, IV skew, Kalshi, VIX regime, etc.) are
    actually calling direction correctly rather than just the composite score.

    history: ascending by timestamp, each with timestamp/spot_price/subscores
    (a dict of category -> raw -1..1 score, as stored in signal_snapshots).
    A category missing from a given snapshot's subscores is treated as neutral
    for that snapshot (excluded from grading), not as a wrong call.

    Returns {category: {"accuracy_pct": float|None, "graded_count": int}}.
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


def inversion_candidates(
    category_accuracy: dict[str, dict], min_graded: int = 10, max_accuracy_pct: float = 40.0,
) -> list[str]:
    """Categories that are reliably *wrong* - enough graded samples and a hit
    rate meaningfully below a coin flip. A consistently-wrong signal is still
    information: its inverse is consistently right. Informational only; the
    user opts in by listing a category under invert_categories in
    config/settings.yaml."""
    return sorted(
        cat for cat, result in category_accuracy.items()
        if result["graded_count"] >= min_graded
        and result["accuracy_pct"] is not None
        and result["accuracy_pct"] <= max_accuracy_pct
    )


def suggest_weights(
    category_accuracy: dict[str, dict], current_weights: dict[str, float], min_graded: int = 10,
) -> dict[str, float] | None:
    """Proposes rebalanced weights based on evaluate_category_accuracy() results -
    informational only, never applied automatically (the user edits config/settings.yaml
    themselves if they agree).

    Only categories with at least min_graded evaluated signals are considered "proven"
    enough to reweight; everything else keeps its current config weight untouched and
    is excluded from the reallocation, so an under-sampled category can't be over- or
    under-weighted on a fluke. Within the eligible set, weight is redistributed
    proportional to how far above a coin-flip (50%) each category's accuracy is - a
    small floor keeps every eligible category present rather than zeroing one out
    entirely on a single bad stretch.

    Returns None if no category has enough graded history yet.
    """
    eligible = {
        cat: result["accuracy_pct"]
        for cat, result in category_accuracy.items()
        if result["graded_count"] >= min_graded
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
    bands: tuple[tuple[float, float], ...] = _DEFAULT_BANDS,
) -> list[dict]:
    """The storable per-ticker calibration map: confidence_calibration() plus
    the numeric band bounds, so calibrated_confidence() can look up which band
    a raw confidence falls into without parsing labels."""
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
            "observed_accuracy_pct": round(hits / len(in_band) * 100.0, 1),
        })
    return results


def calibrated_confidence(
    raw_pct: float, bands: list[dict] | None, min_band_count: int = 5,
) -> float:
    """Maps a raw confidence to the historically observed accuracy of its band
    (from a stored confidence_map). Falls back to the raw value when there is
    no map, the raw value falls outside every stored band, or the matching band
    has too few graded samples to trust. Clamped to 0-100."""
    if not bands:
        return raw_pct
    for band in bands:
        if band["lo"] <= raw_pct < band["hi"]:
            if band.get("count", 0) >= min_band_count:
                return max(0.0, min(100.0, band["observed_accuracy_pct"]))
            return raw_pct
    return raw_pct


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
        }
        for row in rows
    ]


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
    """
    horizon = cfg.get("horizon_minutes", 30)
    actions: list[dict] = []

    category_accuracy = evaluate_category_accuracy(history, horizon)

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
                and result["graded_count"] >= min_graded
                and result["accuracy_pct"] is not None
                and result["accuracy_pct"] <= max_acc):
            actions.append({
                "kind": "uninvert", "category": category,
                "accuracy_pct": result["accuracy_pct"],
            })

    # 3. confidence map: enough graded composite calls -> store observed accuracy per band
    evaluated = evaluate_signal_accuracy(history, horizon)
    graded_count = sum(1 for s in evaluated if s["evaluated"])
    if graded_count >= cfg.get("confidence_min_graded", 20):
        bands = confidence_map(evaluated)
        if bands:
            actions.append({"kind": "confidence_map", "bands": bands})

    return actions
