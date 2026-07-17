"""Event study: when a signal lurched, what did price actually do next?

Every other analysis here averages over all ~10k snapshots and asks "does this
signal predict direction?" (answer, repeatedly: no). That question washes out
rare, violent moments - and market impact concentrates precisely there.
trump_news, for instance, holds one of ~6 values for hours and then jumps: it is
an event stream, not a continuous forecast, and averaging it is the wrong tool.

This module asks a different question: pick the moments a signal MOVED, then
trace the forward price path from each and average across them.

The trap this is built to avoid: "Trump said X and QQQ took off" is the
archetypal narrative fallacy - with enough events you will always find a story.
So every event path is reported against a CONTROL: the same forward path
measured from random non-event moments in the same history. If the event path
and the control path look alike, nothing happened and the story is invented.

Honest scope: with a handful of events this is a forensics tool for looking at
what occurred, NOT a proof engine. It takes on the order of a hundred
independent events before an effect can be claimed. The control baseline and
the reported counts exist to keep that visible.

Pure functions over plain dicts, like the rest of analytics/.
"""
import math
import random
import statistics
from datetime import timedelta

_DEFAULT_OFFSETS = (5, 15, 30, 60, 120, 240)


def detect_signal_shocks(
    history: list[dict], category: str, min_delta: float = 0.3,
    min_gap_minutes: float = 30.0,
) -> list[dict]:
    """Moments where `category`'s subscore lurched by at least min_delta between
    consecutive snapshots.

    min_gap_minutes de-duplicates: one piece of news often nudges a score across
    several cycles, and counting each nudge would triple-count a single event
    (and fake the sample size, the error that has bitten this codebase twice).
    Only the first shock in a window survives.

    Returns [{timestamp, from, to, delta, spot_price}] oldest-first.
    """
    ordered = sorted((h for h in history if h.get("timestamp")),
                     key=lambda h: h["timestamp"])
    shocks: list[dict] = []
    previous = None
    for snap in ordered:
        value = (snap.get("subscores") or {}).get(category)
        if value is None:
            continue
        if previous is not None:
            delta = value - previous
            if abs(delta) >= min_delta:
                if (not shocks or
                        (snap["timestamp"] - shocks[-1]["timestamp"])
                        >= timedelta(minutes=min_gap_minutes)):
                    shocks.append({
                        "timestamp": snap["timestamp"], "category": category,
                        "from": previous, "to": value, "delta": delta,
                        "spot_price": snap.get("spot_price"),
                    })
        previous = value
    return shocks


def detect_price_shocks(
    history: list[dict], min_move_pct: float = 0.4, window_minutes: float = 30.0,
    min_gap_minutes: float = 60.0,
) -> list[dict]:
    """The moves that actually mattered: windows where spot travelled at least
    min_move_pct. Inverts the usual question - instead of 'did our signal work?',
    it asks 'the market went somewhere; did we see it coming?'."""
    ordered = [h for h in sorted((h for h in history if h.get("timestamp")),
                                 key=lambda h: h["timestamp"])
               if h.get("spot_price")]
    shocks: list[dict] = []
    for i, snap in enumerate(ordered):
        target = snap["timestamp"] + timedelta(minutes=window_minutes)
        future = next((l for l in ordered[i + 1:] if l["timestamp"] >= target), None)
        if future is None:
            continue
        move = (future["spot_price"] - snap["spot_price"]) / snap["spot_price"] * 100.0
        if abs(move) >= min_move_pct:
            if (not shocks or (snap["timestamp"] - shocks[-1]["timestamp"])
                    >= timedelta(minutes=min_gap_minutes)):
                shocks.append({
                    "timestamp": snap["timestamp"], "move_pct": move,
                    "spot_price": snap["spot_price"],
                    "direction_called": snap.get("direction"),
                    "composite_score": snap.get("composite_score"),
                })
    return shocks


def forward_path(history: list[dict], event_time, offsets=_DEFAULT_OFFSETS) -> dict:
    """Percent move from the price at event_time to each offset (minutes) after.
    Missing offsets (no snapshot that far ahead) come back None."""
    ordered = [h for h in sorted((h for h in history if h.get("timestamp")),
                                 key=lambda h: h["timestamp"])
               if h.get("spot_price")]
    base = next((h for h in ordered if h["timestamp"] >= event_time), None)
    if base is None:
        return {o: None for o in offsets}

    path = {}
    for offset in offsets:
        target = base["timestamp"] + timedelta(minutes=offset)
        future = next((h for h in ordered if h["timestamp"] >= target), None)
        path[offset] = (
            (future["spot_price"] - base["spot_price"]) / base["spot_price"] * 100.0
            if future is not None else None
        )
    return path


def _mean_path(paths: list[dict], offsets) -> dict:
    out = {}
    for offset in offsets:
        vals = [p[offset] for p in paths if p.get(offset) is not None]
        out[offset] = statistics.fmean(vals) if vals else None
    return out


def event_study(
    history: list[dict], events: list[dict], offsets=_DEFAULT_OFFSETS,
    control_samples: int = 200, signed: bool = True, seed: int = 7,
) -> dict:
    """Average forward path after the events, against a random-moment control.

    signed=True flips the path for negative-delta events, so "did the signal's
    DIRECTION matter?" is asked rather than "did price move?". With signed=False
    you get raw movement, which answers "did volatility pick up?".

    The control draws random moments from the same history, so it carries the
    same drift, session shape and volatility as the events. If event and control
    paths coincide, the event did nothing - however good the story sounds.
    """
    if not events:
        return {"events": 0, "offsets": list(offsets), "event_path": {},
                "control_path": {}, "verdict": "no events detected"}

    event_paths = []
    for event in events:
        path = forward_path(history, event["timestamp"], offsets)
        if signed and event.get("delta", 0) < 0:
            path = {o: (-v if v is not None else None) for o, v in path.items()}
        event_paths.append(path)

    rng = random.Random(seed)
    candidates = [h for h in history if h.get("timestamp") and h.get("spot_price")]
    control_paths = []
    if candidates:
        for _ in range(min(control_samples, len(candidates))):
            pick = rng.choice(candidates)
            control_paths.append(forward_path(history, pick["timestamp"], offsets))

    event_mean = _mean_path(event_paths, offsets)
    control_mean = _mean_path(control_paths, offsets)

    # is the event path distinguishable from the control at any offset?
    detail = {}
    for offset in offsets:
        ev = [p[offset] for p in event_paths if p.get(offset) is not None]
        ct = [p[offset] for p in control_paths if p.get(offset) is not None]
        t = 0.0
        if len(ev) >= 2 and len(ct) >= 2:
            sd = statistics.stdev(ev)
            if sd:
                # event mean vs the control mean, on the events' own spread
                t = (statistics.fmean(ev) - statistics.fmean(ct)) / (sd / math.sqrt(len(ev)))
        detail[offset] = {
            "event_mean_pct": event_mean[offset], "control_mean_pct": control_mean[offset],
            "event_n": len(ev), "t_stat": t,
        }

    strongest = max((abs(d["t_stat"]) for d in detail.values()), default=0.0)
    return {
        "events": len(events), "offsets": list(offsets),
        "event_path": event_mean, "control_path": control_mean,
        "detail": detail, "strongest_t": strongest,
        "verdict": (
            "distinguishable from random moments - worth watching"
            if strongest >= 2 else
            "indistinguishable from random moments - no event effect"
        ),
    }
