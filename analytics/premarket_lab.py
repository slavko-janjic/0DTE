"""Does the premarket (04:00-09:30 ET) say anything the open doesn't?

SPY/QQQ options only trade 09:30-16:00, so premarket is information, not a
trading window. The gap (prior close -> 09:30) is already a lab signal; this
asks about the premarket session itself, scored the way the rest of the lab is.

Pre-registered 2026-10-29, written before any premarket bar was looked at:

Direction - the 10:00 decision, scored with session_lab's option label (ATM
option, the autopilot's exits, costs), follow side only:
- pre_drift: the late-premarket drift, 08:00 -> 09:29 (after the 08:30 data
  releases), in units of its normal size: clip(drift / (2 x 20-day median
  |drift|)).
- pre_levels: where the 10:00 price sits against the premarket high/low
  (04:00-09:29): past the high (+), past the low (-), inside (0), scaled like
  orb15 (0.2% past the level = full strength).

Volatility - does a wide premarket mean a day that moves more than the
option price expects?
- pre_range: the premarket range (high - low) as % of the prior close, over
  its own 20-day median = the day's ratio. WIDE >= 1.5, QUIET <= 1/1.5.
  Outcome per ticker-day: |move 10:00 -> 15:30| / the move the option price
  implies (k x VIX open, the label's vol: S sigma sqrt(T) sqrt(2/pi)).
  Averaged per day within each class (SPY and QQQ are one observation).

Verdicts - 3 tests, noise floor sqrt(2 ln 3) = 1.48:
- direction: session_lab.evaluate_test unchanged (t >= floor, excess
  positive in all four periods, net positive since 2022).
- pre_range: PASS = Welch t of mean(WIDE) - mean(QUIET) >= floor, the
  difference positive in all four periods, and WIDE days moving more than
  implied (mean ratio > 1) since 2022 - the condition for buying to pay.
Descriptive only: how often pre_drift agrees with the gap, and the 10:00
straddle's simulated return on WIDE vs QUIET days.

Premarket bars are thin before ~07:00; a stretch with no print is not a
price. Pure functions; research.py does the I/O.
"""
import math
import statistics
from collections import defaultdict
from datetime import date
from zoneinfo import ZoneInfo

from analytics import option_sim, session_lab

ET = ZoneInfo("America/New_York")
PRE_START = 4 * 60                  # bar index 0 = 04:00-04:01
DRIFT_FROM = 4 * 60                 # 08:00, in minutes since 04:00
PRE_END = 5 * 60 + 30               # 09:30: bars 0..329
MAX_GAP = 30                        # premarket prints are sparse
WIDE = 1.5
EXIT_MINUTE = 360                   # 15:30, the autopilot's cutoff
TESTS = 3


def premarket_from_bars(rows, tz: ZoneInfo = ET) -> dict:
    """{day: {minute since 04:00: Bar}} from (ts, o, h, l, c, v) rows."""
    out: dict = defaultdict(dict)
    for ts, o, h, l, c, v in rows:
        local = ts.astimezone(tz)
        minute = local.hour * 60 + local.minute - PRE_START
        if 0 <= minute < PRE_END:
            out[local.date()][minute] = session_lab.Bar(o, h, l, c, v)
    return dict(out)


def _close_at(bars: dict, minute: int) -> float | None:
    """The last print at or before `minute` (within MAX_GAP)."""
    for back in range(MAX_GAP + 1):
        bar = bars.get(minute - back)
        if bar is not None:
            return bar.close
    return None


def features(bars: dict) -> dict | None:
    """drift (fraction), high, low for one premarket session; None if empty."""
    if not bars:
        return None
    start, end = _close_at(bars, DRIFT_FROM - 1), _close_at(bars, PRE_END - 1)
    return {"drift": (end / start - 1) if start and end else None,
            "high": max(b.high for b in bars.values()),
            "low": min(b.low for b in bars.values())}


def ticker_signals(sessions: list, premarket: dict) -> dict:
    """{day: {'pre_drift': score, 'pre_levels': score, 'range_ratio': x}} for
    one ticker's regular sessions (oldest first). Each day's normal size is
    the median of the LOOKBACK days before it - nothing from the future."""
    out = {}
    drifts, ranges = [], []
    previous = None
    for session in sessions:
        feats = features(premarket.get(session.day, {}))
        scores: dict = {}
        if feats and session.complete():
            spot = option_sim.spot_at(session.path(), session_lab.DECISION_MINUTE)
            if feats["drift"] is not None:
                med = session_lab.median_abs(drifts)
                if med:
                    scores["pre_drift"] = max(-1.0, min(1.0, feats["drift"] / (2 * med)))
            if spot is not None:
                scores["pre_levels"] = session_lab.orb15_score(spot, feats["high"], feats["low"])
            if previous is not None:
                rng = (feats["high"] - feats["low"]) / previous.last_close()
                med = session_lab.median_abs(ranges)
                if med:
                    scores["range_ratio"] = rng / med
        if feats and feats["drift"] is not None:
            drifts.append(feats["drift"])
        if feats and previous is not None:
            ranges.append((feats["high"] - feats["low"]) / previous.last_close())
        if scores:
            out[session.day] = scores
        if session.bars:
            previous = session
    return out


def move_vs_implied(session, sigma: float) -> float | None:
    """|move 10:00 -> 15:30| over the move the option's vol implies."""
    path = session.path()
    start = option_sim.spot_at(path, session_lab.DECISION_MINUTE)
    end = option_sim.spot_at(path, EXIT_MINUTE)
    if start is None or end is None or not sigma:
        return None
    years = (EXIT_MINUTE - session_lab.DECISION_MINUTE) / option_sim._MINUTES_PER_YEAR
    implied = start * sigma * math.sqrt(years) * math.sqrt(2 / math.pi)
    return abs(end - start) / implied if implied else None


def _welch(a: list, b: list) -> float:
    if len(a) < 3 or len(b) < 3:
        return 0.0
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    return (statistics.fmean(a) - statistics.fmean(b)) / se if se else 0.0


def evaluate_range(ratios: dict, outcomes: dict, floor: float) -> dict:
    """The pre_range test. ratios/outcomes: {(ticker, day): value}."""
    classes: dict = {"wide": defaultdict(list), "quiet": defaultdict(list)}
    for key, ratio in ratios.items():
        outcome = outcomes.get(key)
        if outcome is None:
            continue
        cls = "wide" if ratio >= WIDE else "quiet" if ratio <= 1 / WIDE else None
        if cls:
            classes[cls][key[1]].append(outcome)
    per_day = {cls: {d: statistics.fmean(v) for d, v in days.items()}
               for cls, days in classes.items()}

    def values(cls, period=None, since=None):
        return [v for d, v in per_day[cls].items()
                if (period is None or session_lab.period_of(d) == period)
                and (since is None or d.year >= since)]

    periods = {}
    for period, _, _ in session_lab.PERIODS:
        wide, quiet = values("wide", period), values("quiet", period)
        periods[period] = {"wide": statistics.fmean(wide) if wide else None,
                           "quiet": statistics.fmean(quiet) if quiet else None,
                           "days": (len(wide), len(quiet))}
    t = _welch(values("wide"), values("quiet"))
    every = all(p["wide"] is not None and p["quiet"] is not None and p["wide"] > p["quiet"]
                for p in periods.values())
    recent = values("wide", since=session_lab.REAL_0DTE_FROM)
    beats = bool(recent) and statistics.fmean(recent) > 1.0
    strong = t >= floor
    return {"signal": "pre_range", "t": t, "periods": periods,
            "wide_mean": statistics.fmean(values("wide")) if values("wide") else None,
            "quiet_mean": statistics.fmean(values("quiet")) if values("quiet") else None,
            "wide_since_2022": statistics.fmean(recent) if recent else None,
            "days": (len(per_day["wide"]), len(per_day["quiet"])),
            "verdict": ("PASS" if strong and every and beats
                        else "INCONCLUSIVE" if strong or every else "FAIL")}


def straddle_by_class(ratios: dict, labels: dict) -> dict:
    """Descriptive: mean simulated 10:00 straddle return (call + put, % of the
    two premiums) per class, one value per day."""
    out = {}
    for cls, test in (("wide", lambda r: r >= WIDE), ("quiet", lambda r: r <= 1 / WIDE),
                      ("all", lambda r: True)):
        per_day = defaultdict(list)
        for key, ratio in ratios.items():
            label = labels.get(key)
            if label is not None and test(ratio):
                per_day[key[1]].append((label["call"] + label["put"]) / 2)
        days = [statistics.fmean(v) for v in per_day.values()]
        out[cls] = {"mean": statistics.fmean(days) if days else None, "days": len(days)}
    return out


def agreement(signals: dict, name: str, other: str) -> dict:
    """Descriptive: on days both have a non-zero score, how often they agree."""
    pairs = [(s[name], s[other]) for s in signals.values()
             if s.get(name) and s.get(other)]
    same = sum(1 for a, b in pairs if (a > 0) == (b > 0))
    return {"days": len(pairs), "agree_pct": same / len(pairs) * 100 if pairs else None}
