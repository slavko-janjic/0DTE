"""Today's premarket range, live - for the quiet-premarket filter.

Pre-registered and run 2026-10-29 (SIGNAL_RESEARCH.md, "Quiet-premarket
filter"): after a QUIET premarket (04:00-09:29 range at most 2/3 of its own
20-day normal) a bought 10:30 option did worse in every period, and the
morning tactic earned more per trade skipping those days. An agent with
entry `skip_quiet_premarket: true` stands aside on such a ticker-day.

The ratio is computed exactly as the research computed it
(premarket_lab.range_ratio_today), from the same Alpaca SIP minute bars:
about 45 days of premarket + regular-hours bars up to 09:30. The free SIP
feed lags 15 minutes, which the 10:30 entries never notice. Cached per ticker
for the day; a failed fetch is retried after RETRY_SECONDS, and an unknown
ratio never blocks a trade.
"""
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import monotonic

from analytics import premarket_lab, session_lab
from data import history

DAYS_BACK = 45                      # ~30 sessions: 20 for the median + slack for holidays
RETRY_SECONDS = 600

# ticker -> (day, ratio or None, when it was fetched)
_cache: dict[str, tuple[str, float | None, float]] = {}


def _market_hours(ts: datetime) -> bool:
    return history.premarket_hours(ts) or history._regular_hours(ts)


def compute_ratio(ticker: str, today: date, data_dir: Path, fetch=history.fetch_bars) -> float | None:
    keys = history.load_keys(data_dir)
    start = datetime.combine(today - timedelta(days=DAYS_BACK), time(4, 0), tzinfo=history.ET)
    end = datetime.combine(today, time(9, 30), tzinfo=history.ET)
    rows = fetch(ticker, start, end, keys, keep=_market_hours)
    regular = [r for r in rows if history._regular_hours(r[0])]
    pre = premarket_lab.premarket_from_bars([r for r in rows if history.premarket_hours(r[0])])
    return premarket_lab.range_ratio_today(session_lab.sessions_from_bars(regular), pre, today)


def range_ratio(ticker: str, today: date, data_dir: Path, fetch=history.fetch_bars) -> float | None:
    """Today's premarket range over its 20-day normal; None when unknown."""
    cached = _cache.get(ticker)
    if (cached is not None and cached[0] == today.isoformat()
            and (cached[1] is not None or monotonic() - cached[2] < RETRY_SECONDS)):
        return cached[1]
    try:
        ratio = compute_ratio(ticker, today, Path(data_dir), fetch)
    except Exception as exc:  # noqa: BLE001 - no keys, no network: the filter just can't say
        print(f"[{ticker}] premarket range unavailable: {str(exc)[:120]}")
        ratio = None
    _cache[ticker] = (today.isoformat(), ratio, monotonic())
    return ratio
