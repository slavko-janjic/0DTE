"""Signal research lab: does a signal improve the autopilot's trade?

The question is narrow on purpose (spec: docs/superpowers/specs/
2026-10-19-signal-research-lab-design.md). At 10:00 ET the autopilot buys an
at-the-money same-day option on SPY or QQQ and runs it to its exits. Each past
session is labelled with what the call AND the put would have returned
(analytics.option_sim); a signal picks one; its EXCESS is what it earned minus
the coin-flip average of the two. A signal only counts if that excess holds up
in every multi-year period separately - the September review found a signal
whose sign flipped between periods, which an all-history average hides.

Ten pre-declared signals, each tested following and fading: 20 tests, and that
count sets the noise floor. Nothing gets added after seeing results.

Time is minutes since the 09:30 open. A session's bars are keyed by the minute
each bar STARTS (bar 0 = 09:30-09:31); the price known at moment m is the close
of bar m-1, so the 10:00 decision (m = 30) sees bars 0..29.

Pure functions over plain data, like the rest of analytics/.
"""
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from analytics import option_sim
from signals import indicators

ET = ZoneInfo("America/New_York")
DECISION_MINUTE = 30            # 10:00 ET
LOOKBACK = 20                   # sessions behind every "normal size" median
MIN_BARS = 300                  # a session missing more than ~a fifth of its minutes is skipped
FULL_DAY_LAST_BAR = 385         # a half-day stops at bar 209 and is skipped

SIGNALS = ("opening_drive", "gap_direction", "gap_behaviour", "orb15", "technicals",
           "vwap", "prior_levels", "rel_strength", "drive_by_vol", "vix_change")
ORIENTATIONS = {"follow": 1, "fade": -1}
PERIODS = (("2016-2018", 2016, 2018), ("2019-2021", 2019, 2021),
           ("2022-2024", 2022, 2024), ("2025-now", 2025, 9999))
REAL_0DTE_FROM = 2022           # daily same-day SPY/QQQ expiries; earlier ones are synthetic


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, value))


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Session:
    day: date
    bars: dict = field(default_factory=dict)        # bar-start minute -> Bar

    def complete(self) -> bool:
        """Usable for the 10:00 decision and a full afternoon of exits."""
        return (0 in self.bars and DECISION_MINUTE - 1 in self.bars
                and len(self.bars) >= MIN_BARS and max(self.bars) >= FULL_DAY_LAST_BAR)

    def path(self) -> dict:
        """{moment: price known then} - moment m is the close of bar m-1."""
        return {minute + 1: bar.close for minute, bar in self.bars.items()}

    def last_close(self) -> float:
        return self.bars[max(self.bars)].close

    def high(self) -> float:
        return max(b.high for b in self.bars.values())

    def low(self) -> float:
        return min(b.low for b in self.bars.values())


def sessions_from_bars(rows, tz: ZoneInfo = ET) -> list[Session]:
    """Groups (timestamp, open, high, low, close, volume) rows - timestamps
    tz-aware, any zone - into regular-hours sessions in market time, oldest
    first. DST is handled by converting each timestamp, not by a fixed offset."""
    by_day: dict = {}
    for ts, o, h, l, c, v in rows:
        local = ts.astimezone(tz)
        minute = local.hour * 60 + local.minute - (9 * 60 + 30)
        if not 0 <= minute < option_sim.SESSION_MINUTES or c <= 0:
            continue
        session = by_day.setdefault(local.date(), Session(local.date()))
        session.bars[minute] = Bar(o, h, l, c, v)
    return [by_day[d] for d in sorted(by_day)]


# --- signals -----------------------------------------------------------------------

def _median_abs(history: list) -> float | None:
    if len(history) < LOOKBACK:
        return None
    middle = statistics.median(abs(x) for x in history[-LOOKBACK:])
    return middle or None


def _resample_5m(bars: dict, last_minute: int) -> tuple[list, list, list, list]:
    closes, volumes, highs, lows = [], [], [], []
    for start in range(0, last_minute + 1, 5):
        chunk = [bars[m] for m in range(start, min(start + 5, last_minute + 1)) if m in bars]
        if not chunk:
            continue
        closes.append(chunk[-1].close)
        volumes.append(sum(b.volume for b in chunk))
        highs.append(max(b.high for b in chunk))
        lows.append(min(b.low for b in chunk))
    return closes, volumes, highs, lows


def _vix_change(vix_close: dict, day: date) -> float | None:
    prior = [d for d in vix_close if d < day]
    if len(prior) < 2:
        return None
    prior.sort()
    latest, before = vix_close[prior[-1]], vix_close[prior[-2]]
    return (latest - before) / before if before else None


def ticker_signals(sessions: list[Session], vix_close: dict | None = None) -> dict:
    """{day: {signal: score or None, plus '_z' for rel_strength}} for one
    ticker's sessions (oldest first). Only data known at 10:00 is used: today's
    bars 0..29, earlier sessions, and VIX closes before today. Incomplete
    sessions get no scores but still serve as 'the prior session'."""
    vix_close = vix_close or {}
    out: dict = {}
    r30_hist, gap_hist, rv_hist = [], [], []
    previous: Session | None = None
    decision = DECISION_MINUTE - 1
    for session in sessions:
        if not session.complete():
            previous = session if session.bars else previous
            continue
        bars = session.bars
        open_ = bars[0].open
        price = bars[decision].close
        r30 = (price - open_) / open_
        gap = (open_ - previous.last_close()) / previous.last_close() if previous else None
        closes = [bars[m].close for m in range(decision + 1) if m in bars]
        returns = [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
        rv = statistics.stdev(returns) if len(returns) > 2 else None

        med_r30, med_gap, med_rv = _median_abs(r30_hist), _median_abs(gap_hist), _median_abs(rv_hist)
        scores: dict = dict.fromkeys(SIGNALS)

        if med_r30:
            scores["opening_drive"] = _clip(r30 / (2 * med_r30))
            scores["_z"] = r30 / med_r30
        if gap is not None and med_gap:
            scores["gap_direction"] = _clip(gap / (2 * med_gap))
            scores["gap_behaviour"] = (scores["opening_drive"]
                                       if abs(gap) >= med_gap else None)

        first15 = [bars[m] for m in range(15) if m in bars]
        hi, lo = max(b.high for b in first15), min(b.low for b in first15)
        if price > hi:
            scores["orb15"] = _clip((price - hi) / hi * 100.0 / 0.2)
        elif price < lo:
            scores["orb15"] = _clip((price - lo) / lo * 100.0 / 0.2)
        else:
            scores["orb15"] = 0.0

        # exactly what the live worker computes at 10:00 from 5-minute bars
        c5, v5, h5, l5 = _resample_5m(bars, decision)
        scores["technicals"] = indicators.compute_technicals_score(c5, v5, h5, l5)

        volumes = [bars[m].volume for m in range(decision + 1) if m in bars]
        scores["vwap"] = indicators.price_vs_vwap_score(price, indicators.vwap(closes, volumes))

        if previous:
            p_hi, p_lo, p_close = previous.high(), previous.low(), previous.last_close()
            if price > p_hi:
                scores["prior_levels"] = 1.0
            elif price < p_lo:
                scores["prior_levels"] = -1.0
            elif p_hi > p_lo:
                scores["prior_levels"] = _clip((price - p_close) / ((p_hi - p_lo) / 2))

        if rv is not None and med_rv and scores["opening_drive"] is not None:
            scores["drive_by_vol"] = scores["opening_drive"] * (1 if rv / med_rv >= 1 else -1)

        change = _vix_change(vix_close, session.day)
        if change is not None:
            scores["vix_change"] = _clip(-change / 0.10)

        out[session.day] = scores
        r30_hist.append(r30)
        if gap is not None:
            gap_hist.append(gap)
        if rv is not None:
            rv_hist.append(rv)
        previous = session
    return out


def build_signals(sessions_by_ticker: dict, vix_close: dict | None = None) -> dict:
    """{(ticker, day): scores} for every ticker, with rel_strength filled in
    where both tickers have a normalised opening move that day."""
    per_ticker = {t: ticker_signals(s, vix_close) for t, s in sessions_by_ticker.items()}
    tickers = list(per_ticker)
    for ticker in tickers:
        others = [t for t in tickers if t != ticker]
        for day, scores in per_ticker[ticker].items():
            z = scores.get("_z")
            other = per_ticker[others[0]].get(day, {}).get("_z") if len(others) == 1 else None
            if z is not None and other is not None:
                scores["rel_strength"] = _clip((z - other) / 2)
    return {(t, d): {k: v for k, v in s.items() if not k.startswith("_")}
            for t, days in per_ticker.items() for d, s in days.items()}


# --- the label ---------------------------------------------------------------------

def label_session(session: Session, sigma: float, exit_cfg: dict,
                  half_spread: float = 0.0) -> dict | None:
    """What the 10:00 ATM call and put would have returned: {'call': ret%,
    'put': ret%, 'call_exit': reason, 'put_exit': reason}. None when either
    side can't be simulated."""
    path = session.path()
    spot = option_sim.spot_at(path, DECISION_MINUTE)
    if spot is None or sigma is None or sigma <= 0:
        return None
    strike = float(round(spot))
    out = {}
    for side in ("call", "put"):
        trade = option_sim.simulate_trade(path, DECISION_MINUTE, strike, side, sigma,
                                          exit_cfg, half_spread=half_spread)
        if trade is None:
            return None
        out[side], out[f"{side}_exit"] = trade.ret_pct, trade.exit_reason
    return out


# --- scoring -----------------------------------------------------------------------

def _t(values: list) -> float:
    if len(values) < 3:
        return 0.0
    sd = statistics.stdev(values)
    return statistics.fmean(values) / (sd / math.sqrt(len(values))) if sd else 0.0


def noise_floor(tests: int) -> float:
    """The best t-stat pure noise is expected to produce across this many
    tests (same rule as analytics.walkforward.noise_floor_t)."""
    return math.sqrt(2 * math.log(tests)) if tests > 1 else 0.0


def period_of(day: date) -> str | None:
    for name, first, last in PERIODS:
        if first <= day.year <= last:
            return name
    return None


def daily_outcomes(signals: dict, labels: dict, name: str, orientation: int,
                   min_strength: float = 0.0) -> dict:
    """{day: {'excess': mean excess, 'net': mean return, 'wins': n, 'trades': n}}.
    One observation per DAY: SPY and QQQ move together, so counting them as two
    would overstate the sample. A score of 0 or None stands aside."""
    per_day = defaultdict(list)
    for key, label in labels.items():
        score = (signals.get(key) or {}).get(name)
        if score is None or score == 0 or abs(score) < min_strength:
            continue
        side = "call" if orientation * score > 0 else "put"
        ret = label[side]
        per_day[key[1]].append((ret - (label["call"] + label["put"]) / 2, ret))
    return {day: {"excess": statistics.fmean(e for e, _ in rows),
                  "net": statistics.fmean(n for _, n in rows),
                  "wins": sum(1 for _, n in rows if n > 0), "trades": len(rows)}
            for day, rows in per_day.items()}


def evaluate_test(signals: dict, labels: dict, name: str, orientation: int,
                  floor: float, min_strength: float = 0.0) -> dict:
    """One signal in one orientation, with its verdict:
      PASS          - pooled t >= floor, excess positive in every period, and
                      net return positive since real daily 0DTE (2022)
      INCONCLUSIVE  - the t-stat or the every-period condition, not all three
      FAIL          - neither"""
    days = daily_outcomes(signals, labels, name, orientation, min_strength)
    excess = [d["excess"] for d in days.values()]
    by_period = {}
    for period, _, _ in PERIODS:
        rows = [v["excess"] for d, v in days.items() if period_of(d) == period]
        by_period[period] = {"days": len(rows),
                             "mean": statistics.fmean(rows) if rows else None,
                             "t": _t(rows)}
    recent = [v["net"] for d, v in days.items() if d.year >= REAL_0DTE_FROM]
    trades = sum(v["trades"] for v in days.values())
    pooled_t = _t(excess)
    every_period = all(p["mean"] is not None and p["mean"] > 0 for p in by_period.values())
    beats_costs = bool(recent) and statistics.fmean(recent) > 0
    strong = pooled_t >= floor
    verdict = ("PASS" if strong and every_period and beats_costs
               else "INCONCLUSIVE" if strong or every_period else "FAIL")
    return {
        "signal": name, "orientation": "follow" if orientation > 0 else "fade",
        "days": len(days), "trades": trades,
        "mean_excess": statistics.fmean(excess) if excess else None, "t": pooled_t,
        "periods": by_period,
        "net_since_2022": statistics.fmean(recent) if recent else None,
        "win_rate": (sum(v["wins"] for v in days.values()) / trades * 100.0) if trades else None,
        "verdict": verdict,
    }


def null_summary(labels: dict) -> dict:
    """What the blind trade paid, per period: the coin-flip average, and the
    always-call / always-put returns (the market's drift)."""
    per_day = defaultdict(list)
    for (_, day), label in labels.items():
        per_day[day].append(label)
    out = {}
    for period, _, _ in PERIODS:
        rows = [l for d, ls in per_day.items() if period_of(d) == period for l in ls]
        if not rows:
            continue
        out[period] = {
            "sessions": len(rows),
            "coin": statistics.fmean((l["call"] + l["put"]) / 2 for l in rows),
            "call": statistics.fmean(l["call"] for l in rows),
            "put": statistics.fmean(l["put"] for l in rows),
        }
    return out


def run_all(signals: dict, labels: dict, min_strength: float = 0.0) -> list[dict]:
    """Every pre-declared test, strongest pooled t first."""
    floor = noise_floor(len(SIGNALS) * len(ORIENTATIONS))
    results = [evaluate_test(signals, labels, name, sign, floor, min_strength)
               for name in SIGNALS for sign in ORIENTATIONS.values()]
    return sorted(results, key=lambda r: r["t"], reverse=True)
