"""Intraday + events research: does a signal pay later in the session, and do
edges hold or break by weekday and on scheduled events?

Spec: docs/superpowers/specs/2026-10-20-intraday-events-research-design.md.
Extends analytics.session_lab (same label, same verdict rules). 32 pre-declared
main tests - four decision times x four signals x follow/fade - then, for each
pass and for the 10:00 gap_behaviour edge, 13 slices (weekday + 8 event tags).

Time is minutes since the 09:30 open; the price known at minute m is the close
of bar m-1, exactly as in session_lab. Pure functions over plain data.
"""
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from analytics import option_sim, session_lab
from signals import indicators

DECISIONS = {60: "10:30", 120: "11:30", 210: "13:00", 270: "14:00"}
SIGNALS = ("trail30", "session_move", "vwap", "orb15")
MAIN_TESTS = len(DECISIONS) * len(SIGNALS) * len(session_lab.ORIENTATIONS)     # 32
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
EVENT_TAGS = ("fomc", "cpi", "jobs", "opex", "quad_witching", "month_end", "quarter_end",
              "after_megacap_earnings")
SLICES = WEEKDAYS + EVENT_TAGS
MIN_SLICE_DAYS = 30


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _price_at(session: session_lab.Session, minute: int) -> float | None:
    """The price known at `minute`: close of bar minute-1, or the latest bar
    before it (a missing minute isn't a missing price)."""
    return option_sim.spot_at(session.path(), minute)


# --- signals at a decision minute ------------------------------------------------

def ticker_signals_at(sessions: list[session_lab.Session], minute: int) -> dict:
    """{day: {signal: score or None}} at decision `minute` for one ticker's
    sessions (oldest first). Medians use the same minute of the previous
    LOOKBACK complete sessions - a 10:30 move is sized against other 10:30
    moves, not against the whole day."""
    out: dict = {}
    trail_hist, move_hist = [], []
    for session in sessions:
        if not session.complete():
            continue
        bars = session.bars
        price = _price_at(session, minute)
        before = _price_at(session, minute - 30)
        open_ = bars[0].open
        if price is None or before is None:
            continue
        trail = (price - before) / before
        move = (price - open_) / open_
        med_trail = session_lab.median_abs(trail_hist)
        med_move = session_lab.median_abs(move_hist)

        closes = [bars[m].close for m in range(minute) if m in bars]
        volumes = [bars[m].volume for m in range(minute) if m in bars]
        first15 = [bars[m] for m in range(15) if m in bars]
        out[session.day] = {
            "trail30": _clip(trail / (2 * med_trail)) if med_trail else None,
            "session_move": _clip(move / (2 * med_move)) if med_move else None,
            "vwap": indicators.price_vs_vwap_score(price, indicators.vwap(closes, volumes)),
            "orb15": session_lab.orb15_score(price, max(b.high for b in first15),
                                             min(b.low for b in first15)),
        }
        trail_hist.append(trail)
        move_hist.append(move)
    return out


def build_signals_at(sessions_by_ticker: dict, minute: int) -> dict:
    return {(t, d): s for t, ss in sessions_by_ticker.items()
            for d, s in ticker_signals_at(ss, minute).items()}


def build_labels_at(sessions_by_ticker: dict, minute: int, sigma_for, exit_cfg: dict,
                    half_spread: dict, fees: float = 0.0) -> dict:
    """{(ticker, day): label} entering at `minute`. sigma_for(ticker, day) gives
    the volatility (None skips the session)."""
    labels = {}
    for ticker, sessions in sessions_by_ticker.items():
        for session in sessions:
            if not session.complete():
                continue
            sigma = sigma_for(ticker, session.day)
            if sigma is None:
                continue
            label = session_lab.label_session(session, sigma, exit_cfg,
                                              half_spread.get(ticker, 0.0), decision_minute=minute,
                                              fees=fees)
            if label is not None:
                labels[(ticker, session.day)] = label
    return labels


def run_main_tests(signals_by_time: dict, labels_by_time: dict) -> list[dict]:
    """All 32 main tests, strongest t first, each tagged with its decision time."""
    floor = session_lab.noise_floor(MAIN_TESTS)
    results = []
    for minute, clock in DECISIONS.items():
        for name in SIGNALS:
            for sign in session_lab.ORIENTATIONS.values():
                r = session_lab.evaluate_test(signals_by_time[minute], labels_by_time[minute],
                                              name, sign, floor)
                r["minute"], r["time"] = minute, clock
                results.append(r)
    return sorted(results, key=lambda r: r["t"], reverse=True)


# --- the event calendar --------------------------------------------------------------

def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def event_tags(trading_days: list[date], fomc: set, cpi: set, jobs: set,
               earnings: list[tuple[date, str]]) -> dict:
    """{day: set of tags} over the given trading days (sorted, all sessions).

    opex = the 3rd Friday, or the last session before it when that Friday is a
    holiday; quad_witching = opex in Mar/Jun/Sep/Dec; month_end / quarter_end =
    the last session of the month / quarter; after_megacap_earnings = the first
    session that trades on the news (amc -> next session, bmo -> same day).
    An FOMC / CPI / jobs date outside a session (the Sunday 2020-03-15
    emergency cut) tags the next session - the first one that trades on it."""
    days = sorted(trading_days)
    day_set = set(days)
    tags: dict = defaultdict(set)

    def first_session_from(day: date) -> date | None:
        if day in day_set:
            return day
        later = next((d for d in days if d > day), None)
        return later if later is not None and (later - day).days <= 3 else None

    for name, dates in (("fomc", fomc), ("cpi", cpi), ("jobs", jobs)):
        for event_day in dates:
            trade_day = first_session_from(event_day)
            if trade_day is not None:
                tags[trade_day].add(name)

    months = sorted({(d.year, d.month) for d in days})
    for year, month in months:
        in_month = [d for d in days if (d.year, d.month) == (year, month)]
        friday = third_friday(year, month)
        expiry = max((d for d in in_month if d <= friday), default=None)
        if expiry is not None and (friday - expiry).days <= 3:
            tags[expiry].add("opex")
            if month in (3, 6, 9, 12):
                tags[expiry].add("quad_witching")
        last = in_month[-1]
        tags[last].add("month_end")
        if month in (3, 6, 9, 12):
            tags[last].add("quarter_end")

    for report_day, timing in earnings:
        if timing == "bmo":
            trade_day = first_session_from(report_day)
        else:
            trade_day = next((d for d in days if d > report_day), None)
        if trade_day is not None:
            tags[trade_day].add("after_megacap_earnings")
    return dict(tags)


def day_slices(day: date, tags: dict) -> set:
    return {WEEKDAYS[day.weekday()]} | tags.get(day, set())


# --- slices ------------------------------------------------------------------------

def _welch_t(a: list, b: list) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = statistics.variance(a), statistics.variance(b)
    denom = math.sqrt(va / len(a) + vb / len(b))
    return (statistics.fmean(a) - statistics.fmean(b)) / denom if denom else 0.0


def slice_report(signals: dict, labels: dict, name: str, orientation: int, tags: dict,
                 covered: dict | None = None) -> list[dict]:
    """Does the edge hold on each slice? One row per slice: days, mean excess
    in and outside it, Welch t of the difference, and a verdict. `covered`
    maps a slice to the (first, last) dates its source covers - days outside
    are left out of both sides (e.g. earnings dates that don't reach 2016)."""
    floor = session_lab.noise_floor(len(SLICES))
    days = session_lab.daily_outcomes(signals, labels, name, orientation)
    rows = []
    for slice_name in SLICES:
        span = (covered or {}).get(slice_name)
        pool = {d: v for d, v in days.items() if span is None or span[0] <= d <= span[1]}
        inside = [v["excess"] for d, v in pool.items() if slice_name in day_slices(d, tags)]
        outside = [v["excess"] for d, v in pool.items() if slice_name not in day_slices(d, tags)]
        t = _welch_t(inside, outside)
        mean_in = statistics.fmean(inside) if inside else None
        if len(inside) < MIN_SLICE_DAYS:
            verdict = "too few days"
        elif mean_in <= 0 and t <= -floor:
            verdict = "breaks"
        elif mean_in > 0:
            verdict = "holds"
        else:
            verdict = "weaker, not significant"
        rows.append({"slice": slice_name, "days": len(inside), "mean_in": mean_in,
                     "mean_out": statistics.fmean(outside) if outside else None,
                     "t_diff": t, "verdict": verdict})
    return rows


def blind_profile(labels: dict, tags: dict) -> dict:
    """Coin-flip return by slice (descriptive only - see the spec)."""
    by_slice = defaultdict(list)
    for (_, day), label in labels.items():
        coin = (label["call"] + label["put"]) / 2
        by_slice["all"].append(coin)
        for s in day_slices(day, tags):
            by_slice[s].append(coin)
    return {s: {"n": len(v), "coin": statistics.fmean(v)} for s, v in by_slice.items()}
