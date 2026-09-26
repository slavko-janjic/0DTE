"""explain_auto_decision: the autopilot's entry decision WITH its reasoning.
Pure (no DB), so we assert both the verdict and the plain-language blocker, and
that should_auto_enter stays a faithful wrapper over it."""
from datetime import datetime
from zoneinfo import ZoneInfo

from paper_trading.engine import explain_auto_decision, should_auto_enter

TZ = "America/New_York"
NOW = datetime(2026, 9, 21, 10, 15, tzinfo=ZoneInfo(TZ))  # 45 min into a session

CFG = {
    "min_confidence_pct": 55, "tactic": "opening_range",
    "decision_start_minutes": 30, "decision_end_minutes": 90,
    "no_entry_last_minutes": 60, "max_concurrent_positions": 3,
    "max_trades_per_day": 4, "max_entries_per_session": 2,
    "cooldown_minutes": 30, "daily_loss_limit_pct": 10,
    "no_entry_before_catalyst_minutes": 15, "positive_gamma_confidence_penalty": 10,
}


def _intent(**kw):
    base = dict(
        ticker="SPY", direction="bearish", confidence_pct=61.0,
        minutes_since_open=60, minutes_to_close=300, open_rows=[], closed_rows=[],
        autopilot_cfg=CFG, loss_limit_base=10000, now=NOW, tz_name=TZ,
        minutes_to_catalyst=None, gamma_regime=None,
    )
    base.update(kw)
    return explain_auto_decision(**base)


def test_arms_when_all_guards_clear():
    i = _intent()
    assert i.would_enter is True
    assert i.lean == "put"
    assert i.blocker is None
    assert i.min_confidence_pct == 55


def test_below_confidence_gate():
    i = _intent(confidence_pct=48.0)
    assert i.would_enter is False
    assert i.lean == "put"           # still knows the lean, just won't fire
    assert "below the 55% gate" in i.blocker


def test_neutral_has_no_lean():
    i = _intent(direction="neutral")
    assert i.would_enter is False
    assert i.lean is None
    assert "neutral" in i.blocker


def test_before_decision_window_reports_countdown():
    i = _intent(minutes_since_open=20)   # window opens at 30
    assert i.would_enter is False
    assert "opens in 10 min" in i.blocker


def test_after_decision_window_closes():
    i = _intent(minutes_since_open=100)  # window closed at 90
    assert i.would_enter is False
    assert "closed for today" in i.blocker


def test_positive_gamma_raises_the_gate():
    # 61% clears the 55% base gate but not the 65% raised one
    i = _intent(gamma_regime="positive")
    assert i.min_confidence_pct == 65
    assert i.would_enter is False
    assert "65% gate" in i.blocker
    # ...but a strong enough signal still fires
    assert _intent(confidence_pct=70.0, gamma_regime="positive").would_enter is True


def test_catalyst_proximity_stands_down():
    i = _intent(minutes_to_catalyst=10)
    assert i.would_enter is False
    assert "catalyst" in i.blocker


def test_session_cap_blocks_after_todays_entries():
    today = NOW.isoformat()
    closed = [
        {"ticker": "QQQ", "opened_by": "auto", "entry_time": today, "exit_time": None, "pnl": 0.0},
        {"ticker": "QQQ", "opened_by": "auto", "entry_time": today, "exit_time": None, "pnl": 0.0},
    ]
    i = _intent(closed_rows=closed)
    assert i.would_enter is False
    assert "session" in i.blocker


def test_already_holding_blocks():
    i = _intent(open_rows=[{"ticker": "SPY", "opened_by": "manual", "entry_time": NOW.isoformat()}])
    assert i.would_enter is False
    assert "already holding" in i.blocker


def test_should_auto_enter_is_a_faithful_wrapper():
    common = dict(
        minutes_since_open=60, minutes_to_close=300, open_rows=[], closed_rows=[],
        autopilot_cfg=CFG, loss_limit_base=10000, now=NOW, tz_name=TZ,
    )
    # would-enter case -> returns the lean
    assert should_auto_enter(ticker="SPY", direction="bearish", confidence_pct=61.0, **common) == "put"
    # blocked case -> returns None
    assert should_auto_enter(ticker="SPY", direction="bearish", confidence_pct=40.0, **common) is None
    # neutral -> None
    assert should_auto_enter(ticker="SPY", direction="neutral", confidence_pct=90.0, **common) is None
