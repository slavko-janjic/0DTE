from datetime import date, datetime, timezone

import pytest

from paper_trading.engine import (
    AUTO_CLOSE_REASONS, buy, calculate_contracts, calculate_pnl, close, daily_realized_pnl,
    evaluate_exit, month_calendar_cells, price_target_exit, shift_month, should_auto_enter,
    summarize_pnl, trade_events,
)
from storage import db as storage
from paper_trading.models import Position

EXIT_RULES = {
    "profit_target_pct": 50,
    "stop_loss_pct": -30,
    "time_cutoff_minutes_before_close": 30,
    "reversal_confidence_pct": 60,
}


def make_position(option_type="call", entry_price=2.0, contracts=5) -> Position:
    return Position(
        id=1, ticker="QQQ", option_type=option_type, strike=450, expiration="2026-07-05",
        contracts=contracts, entry_price=entry_price, cost_basis=entry_price * contracts * 100,
        entry_composite_score=0.7,
    )


def test_calculate_contracts_respects_risk_budget():
    # $10,000 balance, 5% risk = $500 budget, $2.00 premium = $200/contract -> 2 contracts
    assert calculate_contracts(balance=10000, risk_per_trade_pct=5, entry_price=2.0) == 2


def test_calculate_contracts_zero_when_too_expensive():
    assert calculate_contracts(balance=1000, risk_per_trade_pct=5, entry_price=100.0) == 0


def test_calculate_pnl():
    pnl_dollars, pnl_pct = calculate_pnl(entry_price=2.0, exit_price=3.0, contracts=5)
    assert pnl_dollars == 500.0
    assert pnl_pct == pytest.approx(50.0)


def test_evaluate_exit_time_cutoff_overrides_everything():
    position = make_position()
    reason = evaluate_exit(position, current_price=2.0, current_composite_score=0.8,
                            minutes_to_close=10, exit_rules=EXIT_RULES)
    assert reason == "time_cutoff"


def test_evaluate_exit_profit_target():
    position = make_position(entry_price=2.0)
    position.profit_target_pct = 50  # per-trade target
    reason = evaluate_exit(position, current_price=3.5, current_composite_score=0.7,
                            minutes_to_close=120, exit_rules=EXIT_RULES)  # +75%
    assert reason == "profit_target"


def test_evaluate_exit_stop_loss():
    position = make_position(entry_price=2.0)
    position.stop_loss_pct = -30  # per-trade stop
    reason = evaluate_exit(position, current_price=1.0, current_composite_score=0.7,
                            minutes_to_close=120, exit_rules=EXIT_RULES)  # -50%
    assert reason == "stop_loss"


def test_price_target_exit_fires_only_when_set_and_breached():
    p = make_position(entry_price=2.0)
    p.profit_target_pct = 50
    assert price_target_exit(p, 3.5) == "profit_target"   # +75%
    assert price_target_exit(p, 2.5) is None               # +25%, below target

    p2 = make_position(entry_price=2.0)
    p2.stop_loss_pct = -30
    assert price_target_exit(p2, 1.0) == "stop_loss"       # -50%

    p3 = make_position(entry_price=2.0)                     # nothing set
    assert price_target_exit(p3, 10.0) is None


def test_close_is_idempotent_and_does_not_double_credit(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    storage.ensure_account(path, 10000.0)
    pid = buy(path, "QQQ", "call", 450.0, "2026-07-07", 2.0, 1, 0.5)  # cost 200 -> 9800
    assert storage.get_balance(path) == 9800.0

    pnl1 = close(path, pid, 3.0, "manual")   # +100 -> 9800 + 200 + 100
    assert pnl1 == 100.0
    assert storage.get_balance(path) == 10100.0

    # closing again (e.g. worker + dashboard racing) must be a no-op
    pnl2 = close(path, pid, 3.0, "manual")
    assert pnl2 is None
    assert storage.get_balance(path) == 10100.0


def test_auto_close_reasons_are_only_per_trade_targets():
    # the worker auto-closes on these; time-cutoff / reversal stay suggestion-only
    assert AUTO_CLOSE_REASONS == {"profit_target", "stop_loss"}
    assert "time_cutoff" not in AUTO_CLOSE_REASONS
    assert "signal_reversal" not in AUTO_CLOSE_REASONS


def test_evaluate_exit_profit_stop_off_by_default():
    # both targets None -> even a huge gain does not auto-exit (only the global
    # time-cutoff / reversal safety nets can fire)
    position = make_position(entry_price=2.0)
    assert position.profit_target_pct is None and position.stop_loss_pct is None
    reason = evaluate_exit(position, current_price=10.0, current_composite_score=0.1,
                            minutes_to_close=120, exit_rules=EXIT_RULES)  # +400%
    assert reason is None


def test_evaluate_exit_signal_reversal_on_call_position():
    position = make_position(option_type="call", entry_price=2.0)
    # price flat (no profit/stop trigger), but signal has reversed hard bearish
    reason = evaluate_exit(position, current_price=2.05, current_composite_score=-0.75,
                            minutes_to_close=120, exit_rules=EXIT_RULES)
    assert reason == "signal_reversal"


def test_evaluate_exit_no_reversal_for_matching_direction():
    position = make_position(option_type="call", entry_price=2.0)
    reason = evaluate_exit(position, current_price=2.05, current_composite_score=0.75,
                            minutes_to_close=120, exit_rules=EXIT_RULES)
    assert reason is None


def test_evaluate_exit_none_when_nothing_fires():
    position = make_position(entry_price=2.0)
    reason = evaluate_exit(position, current_price=2.05, current_composite_score=0.1,
                            minutes_to_close=120, exit_rules=EXIT_RULES)
    assert reason is None


# summarize_pnl works on plain row dicts; "now" is injectable so the
# today-vs-yesterday boundary is deterministic. 2026-07-06 16:00 UTC = noon ET.
_NOW = datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc)


def _closed(pnl, exit_time):
    return {"pnl": pnl, "exit_time": exit_time}


def _open(entry_price, current_price, contracts=1):
    return {"entry_price": entry_price, "current_price": current_price, "contracts": contracts}


def test_summarize_pnl_splits_today_from_total_realized():
    closed = [
        _closed(100.0, "2026-07-06T14:00:00+00:00"),  # today (ET)
        _closed(-40.0, "2026-07-02T14:00:00+00:00"),  # earlier trading day
    ]
    summary = summarize_pnl([], closed, now=_NOW)
    assert summary["realized_today"] == 100.0
    assert summary["realized_total"] == 60.0


def test_summarize_pnl_exit_near_midnight_uses_market_timezone():
    # 2026-07-07T02:00 UTC is still 2026-07-06 22:00 in New York -> counts as today
    closed = [_closed(50.0, "2026-07-07T02:00:00+00:00")]
    summary = summarize_pnl([], closed, now=_NOW)
    assert summary["realized_today"] == 50.0


def test_summarize_pnl_sums_unrealized_and_skips_unpriced():
    open_rows = [
        _open(entry_price=2.0, current_price=3.0),   # +100
        _open(entry_price=2.0, current_price=1.5),   # -50
        _open(entry_price=2.0, current_price=None),  # skipped
    ]
    summary = summarize_pnl(open_rows, [], now=_NOW)
    assert summary["unrealized_open"] == 50.0


def test_summarize_pnl_empty_inputs():
    summary = summarize_pnl([], [], now=_NOW)
    assert summary == {"realized_today": 0.0, "realized_total": 0.0, "unrealized_open": 0.0}


def test_daily_realized_pnl_buckets_by_market_tz_date():
    closed = [
        _closed(100.0, "2026-07-06T14:00:00+00:00"),   # Jul 6 ET
        _closed(-30.0, "2026-07-06T18:00:00+00:00"),   # Jul 6 ET, same bucket
        # 02:00 UTC Jul 7 is still 22:00 Jul 6 in New York
        _closed(10.0, "2026-07-07T02:00:00+00:00"),
        _closed(-40.0, "2026-07-02T14:00:00+00:00"),   # earlier day
    ]
    by_day = daily_realized_pnl(closed)
    assert by_day[datetime(2026, 7, 6).date()] == pytest.approx(80.0)
    assert by_day[datetime(2026, 7, 2).date()] == pytest.approx(-40.0)
    assert len(by_day) == 2


def test_daily_realized_pnl_skips_rows_without_exit_time():
    assert daily_realized_pnl([_closed(50.0, None)]) == {}


def test_daily_realized_pnl_empty():
    assert daily_realized_pnl([]) == {}


def _trade_row(ticker, entry_time, exit_time=None, pnl=None):
    return {
        "ticker": ticker, "option_type": "call", "strike": 500.0,
        "entry_price": 2.0, "entry_time": entry_time,
        "exit_price": 3.0, "exit_time": exit_time, "pnl": pnl,
    }


def test_trade_events_open_gives_entry_only_closed_gives_both():
    open_rows = [_trade_row("QQQ", "2026-07-07T14:00:00+00:00")]
    closed_rows = [_trade_row("QQQ", "2026-07-06T14:00:00+00:00",
                              exit_time="2026-07-06T15:30:00+00:00", pnl=100.0)]
    events = trade_events(open_rows, closed_rows, "QQQ")
    kinds = sorted(e["kind"] for e in events)
    assert kinds == ["Entry", "Entry", "Exit"]  # open entry + closed entry + closed exit
    exit_event = next(e for e in events if e["kind"] == "Exit")
    assert "P&L $+100" in exit_event["label"]


def test_trade_events_filters_by_ticker():
    open_rows = [_trade_row("QQQ", "2026-07-07T14:00:00+00:00"),
                 _trade_row("SPY", "2026-07-07T14:00:00+00:00")]
    events = trade_events(open_rows, [], "QQQ")
    assert len(events) == 1
    assert events[0]["time"] == datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


def test_trade_events_empty():
    assert trade_events([], [], "QQQ") == []


def test_shift_month_within_year():
    assert shift_month(2026, 3, 1) == (2026, 4)
    assert shift_month(2026, 3, -1) == (2026, 2)


def test_shift_month_rolls_over_year():
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)


def test_shift_month_multi_step():
    assert shift_month(2026, 6, 8) == (2027, 2)
    assert shift_month(2026, 6, -8) == (2025, 10)


def test_month_calendar_cells_shape_and_in_month_flags():
    # July 2026: the 1st is a Wednesday, so a Sunday-first grid starts with
    # spillover days from June (28, 29, 30) then July 1.
    weeks = month_calendar_cells({}, 2026, 7)
    assert all(len(week) == 7 for week in weeks)
    first_week = weeks[0]
    # leading cells belong to June (in_month False), then July 1 appears
    assert first_week[0]["in_month"] is False
    july_first = next(cell for week in weeks for cell in week
                      if cell["date"] == date(2026, 7, 1))
    assert july_first["in_month"] is True
    assert july_first["day"] == 1


def test_month_calendar_cells_maps_pnl_to_correct_day():
    pnl_by_day = {date(2026, 7, 6): 125.0, date(2026, 7, 7): -40.0}
    weeks = month_calendar_cells(pnl_by_day, 2026, 7)
    cells = {cell["date"]: cell["pnl"] for week in weeks for cell in week}
    assert cells[date(2026, 7, 6)] == 125.0
    assert cells[date(2026, 7, 7)] == -40.0
    # a day with no trades has pnl None
    assert cells[date(2026, 7, 8)] is None


def test_month_calendar_cells_empty_pnl_still_builds_grid():
    weeks = month_calendar_cells({}, 2026, 2)  # Feb 2026
    assert len(weeks) >= 4
    assert all(cell["pnl"] is None for week in weeks for cell in week)


# --- auto-pilot entry decision ------------------------------------------------

AUTOPILOT_CFG = {
    "min_confidence_pct": 55,
    "max_concurrent_positions": 3,
    "max_trades_per_day": 4,
    "cooldown_minutes": 30,
    "daily_loss_limit_pct": 10,
    "no_entry_first_minutes": 30,
    "no_entry_last_minutes": 60,
}
_AP_NOW = datetime(2026, 7, 7, 16, 0, tzinfo=timezone.utc)  # 12:00 ET


def _ap_row(ticker="SPY", opened_by="auto", entry_time="2026-07-07T14:00:00+00:00",
            exit_time=None, pnl=None):
    return {"ticker": ticker, "opened_by": opened_by,
            "entry_time": entry_time, "exit_time": exit_time, "pnl": pnl}


def _enter(ticker="QQQ", direction="bullish", confidence=60.0, since_open=120,
           to_close=180, open_rows=(), closed_rows=(), cfg=AUTOPILOT_CFG,
           minutes_to_catalyst=None, gamma_regime=None):
    return should_auto_enter(
        ticker=ticker, direction=direction, confidence_pct=confidence,
        minutes_since_open=since_open, minutes_to_close=to_close,
        open_rows=list(open_rows), closed_rows=list(closed_rows),
        autopilot_cfg=cfg, starting_balance=10000.0, now=_AP_NOW, tz_name="UTC",
        minutes_to_catalyst=minutes_to_catalyst, gamma_regime=gamma_regime,
    )


def test_should_auto_enter_happy_paths():
    assert _enter(direction="bullish") == "call"
    assert _enter(direction="bearish") == "put"


def test_should_auto_enter_blocks_neutral_and_low_confidence():
    assert _enter(direction="neutral") is None
    assert _enter(confidence=54.9) is None


def test_should_auto_enter_respects_entry_window():
    assert _enter(since_open=20) is None      # opening range still forming
    assert _enter(to_close=45) is None        # too close to the bell


def test_should_auto_enter_never_stacks_on_open_position_in_ticker():
    # even a MANUAL open position in the ticker blocks auto entry
    open_rows = [_ap_row(ticker="QQQ", opened_by="manual")]
    assert _enter(open_rows=open_rows) is None
    # a position in a different ticker doesn't block
    assert _enter(open_rows=[_ap_row(ticker="SPY")]) == "call"


def test_should_auto_enter_concurrent_cap_counts_auto_only():
    three_auto = [_ap_row(ticker=t, opened_by="auto") for t in ("SPY", "IWM", "TSLA")]
    assert _enter(open_rows=three_auto) is None
    # manual positions don't count against the auto cap
    three_manual = [_ap_row(ticker=t, opened_by="manual") for t in ("SPY", "IWM", "TSLA")]
    assert _enter(open_rows=three_manual) == "call"


def test_should_auto_enter_daily_trade_cap():
    four_today = [
        _ap_row(ticker="SPY", exit_time="2026-07-07T15:00:00+00:00", pnl=10.0)
        for _ in range(4)
    ]
    # cooldown would also block SPY; use a different ticker to isolate the cap
    assert _enter(ticker="QQQ", closed_rows=four_today) is None


def test_should_auto_enter_cooldown_after_close_in_ticker():
    recently_closed = [_ap_row(ticker="QQQ", opened_by="manual",
                               exit_time="2026-07-07T15:45:00+00:00", pnl=5.0)]  # 15 min ago
    assert _enter(ticker="QQQ", closed_rows=recently_closed) is None
    # same close but 45 min ago -> outside the 30-min cooldown
    older_close = [_ap_row(ticker="QQQ", opened_by="manual",
                           exit_time="2026-07-07T15:15:00+00:00", pnl=5.0)]
    assert _enter(ticker="QQQ", closed_rows=older_close) == "call"


def test_should_auto_enter_circuit_breaker():
    big_loss_today = [_ap_row(ticker="SPY", opened_by="auto",
                              exit_time="2026-07-07T15:00:00+00:00", pnl=-1000.0)]  # -10% of 10k
    assert _enter(ticker="QQQ", closed_rows=big_loss_today) is None
    # manual losses don't trip the AUTO breaker
    manual_loss = [_ap_row(ticker="SPY", opened_by="manual",
                           exit_time="2026-07-07T15:00:00+00:00", pnl=-1000.0)]
    assert _enter(ticker="QQQ", closed_rows=manual_loss) == "call"


OPENING_RANGE_CFG = {
    **AUTOPILOT_CFG,
    "tactic": "opening_range",
    "decision_start_minutes": 30,
    "decision_end_minutes": 90,
    "max_entries_per_session": 1,
}


def test_opening_range_tactic_decision_window():
    # before the window opens
    assert _enter(since_open=20, cfg=OPENING_RANGE_CFG) is None
    # inside the window (boundaries inclusive)
    assert _enter(since_open=30, cfg=OPENING_RANGE_CFG) == "call"
    assert _enter(since_open=60, cfg=OPENING_RANGE_CFG) == "call"
    assert _enter(since_open=90, cfg=OPENING_RANGE_CFG) == "call"
    # after the window closes - stand down for the day
    assert _enter(since_open=120, cfg=OPENING_RANGE_CFG) is None


def test_opening_range_tactic_respects_last_minutes_runway():
    # half-day style: window is open but too little runway before the bell
    assert _enter(since_open=60, to_close=45, cfg=OPENING_RANGE_CFG) is None


def test_opening_range_tactic_one_entry_per_session():
    # one auto entry already made today (even in another ticker, already closed)
    prior = [_ap_row(ticker="SPY", opened_by="auto",
                     entry_time="2026-07-07T14:30:00+00:00",
                     exit_time="2026-07-07T15:00:00+00:00", pnl=25.0)]
    assert _enter(ticker="QQQ", since_open=60, closed_rows=prior,
                  cfg=OPENING_RANGE_CFG) is None
    # continuous tactic with the same history still allows entry (day cap is 4)
    assert _enter(ticker="QQQ", since_open=60, closed_rows=prior) == "call"
    # a manual trade today does NOT consume the session's single auto entry
    manual = [_ap_row(ticker="SPY", opened_by="manual",
                      entry_time="2026-07-07T14:30:00+00:00",
                      exit_time="2026-07-07T15:00:00+00:00", pnl=25.0)]
    assert _enter(ticker="QQQ", since_open=60, closed_rows=manual,
                  cfg=OPENING_RANGE_CFG) == "call"


def test_continuous_tactic_unchanged_by_new_keys():
    # explicit continuous ignores the opening-range window entirely
    cfg = {**OPENING_RANGE_CFG, "tactic": "continuous"}
    assert _enter(since_open=120, cfg=cfg) == "call"
    assert _enter(since_open=20, cfg=cfg) is None


def test_should_auto_enter_catalyst_guard_blocks_inside_window():
    # a high-impact catalyst 10 min away, guard is 15 min -> blocked
    assert _enter(minutes_to_catalyst=10.0) is None


def test_should_auto_enter_catalyst_guard_allows_outside_window():
    # 40 min away -> fine
    assert _enter(minutes_to_catalyst=40.0) == "call"
    # exactly at the boundary is still blocked (<=)
    assert _enter(minutes_to_catalyst=15.0) is None
    # no catalyst -> unaffected
    assert _enter(minutes_to_catalyst=None) == "call"


GAMMA_CFG = {**AUTOPILOT_CFG, "positive_gamma_confidence_penalty": 10}


def test_positive_gamma_raises_the_confidence_bar():
    # 60% clears the base 55% bar, but positive gamma pushes it to 65% -> blocked
    assert _enter(confidence=60.0, cfg=GAMMA_CFG, gamma_regime="positive") is None
    # a stronger 70% call still gets through in the same regime
    assert _enter(confidence=70.0, cfg=GAMMA_CFG, gamma_regime="positive") == "call"


def test_negative_and_neutral_gamma_leave_threshold_unchanged():
    assert _enter(confidence=60.0, cfg=GAMMA_CFG, gamma_regime="negative") == "call"
    assert _enter(confidence=60.0, cfg=GAMMA_CFG, gamma_regime="neutral") == "call"
    assert _enter(confidence=60.0, cfg=GAMMA_CFG, gamma_regime=None) == "call"


def test_positive_gamma_penalty_off_by_default():
    # AUTOPILOT_CFG has no penalty key -> positive gamma has no effect
    assert _enter(confidence=60.0, cfg=AUTOPILOT_CFG, gamma_regime="positive") == "call"


# --- trailing stop + late-session (theta) stop ------------------------------

TRAIL_RULES = {**EXIT_RULES, "trailing_activate_pct": 30, "trailing_stop_pct": 15,
               "late_session_minutes": 90, "late_session_stop_pct": -15}


def _pos(entry=2.0, contracts=1, option_type="call", max_price=None):
    p = make_position(option_type=option_type, entry_price=entry, contracts=contracts)
    p.max_price = max_price
    return p


def test_trailing_stop_not_armed_below_activation():
    from paper_trading.engine import trailing_stop_exit
    # peaked at +20% (< 30% activation): trailing never arms even on a pullback
    p = _pos(entry=2.0, max_price=2.4)
    assert trailing_stop_exit(p, 2.0, TRAIL_RULES) is None


def test_trailing_stop_fires_after_arming_and_giveback():
    from paper_trading.engine import trailing_stop_exit
    # peaked at +50% (2.0 -> 3.0), now back to 2.5: gave back (3.0-2.5)/3.0 = 16.7% >= 15%
    p = _pos(entry=2.0, max_price=3.0)
    assert trailing_stop_exit(p, 2.5, TRAIL_RULES) == "trailing_stop"
    # small pullback to 2.8: gave back only 6.7% -> hold
    assert trailing_stop_exit(p, 2.8, TRAIL_RULES) is None


def test_trailing_stop_off_when_unconfigured():
    from paper_trading.engine import trailing_stop_exit
    p = _pos(entry=2.0, max_price=3.0)
    assert trailing_stop_exit(p, 2.5, EXIT_RULES) is None  # no trailing keys


def test_late_session_stop_cuts_lingering_loser():
    from paper_trading.engine import late_session_stop_exit
    p = _pos(entry=2.0)
    # in the last 90 min, down 20% (2.0 -> 1.6) -> cut
    assert late_session_stop_exit(p, 1.6, minutes_to_close=60, exit_rules=TRAIL_RULES) == "time_decay_stop"
    # same loss but earlier in the session -> hold
    assert late_session_stop_exit(p, 1.6, minutes_to_close=180, exit_rules=TRAIL_RULES) is None
    # late but only down 5% -> hold
    assert late_session_stop_exit(p, 1.9, minutes_to_close=60, exit_rules=TRAIL_RULES) is None


def test_evaluate_exit_priority_trailing_before_reversal():
    # armed & faded winner should exit as trailing_stop even if signal also reversed
    p = _pos(entry=2.0, max_price=3.0, option_type="call")
    reason = evaluate_exit(p, current_price=2.5, current_composite_score=-0.9,
                            minutes_to_close=180, exit_rules=TRAIL_RULES)
    assert reason == "trailing_stop"


def test_auto_force_reasons_membership():
    from paper_trading.engine import AUTO_FORCE_REASONS
    assert {"trailing_stop", "time_decay_stop", "time_cutoff", "catalyst"} == set(AUTO_FORCE_REASONS)
    assert "profit_target" not in AUTO_FORCE_REASONS  # that's AUTO_CLOSE_REASONS (force for all)


# --- review fixes: exit priority, settlement, atomic balance ----------------

def test_per_trade_stop_beats_the_time_cutoff():
    # the worker only SUGGESTS a time cutoff for manual positions, so if the
    # cutoff won here a manual stop was silently disarmed for the last 30 min
    position = make_position(entry_price=2.0)
    position.stop_loss_pct = -35
    assert evaluate_exit(position, 0.60, 0.0, minutes_to_close=20,
                         exit_rules=EXIT_RULES) == "stop_loss"
    position.stop_loss_pct, position.profit_target_pct = None, 50
    assert evaluate_exit(position, 3.5, 0.0, minutes_to_close=20,
                         exit_rules=EXIT_RULES) == "profit_target"


def test_settlement_price_is_intrinsic_value():
    from paper_trading.engine import settlement_price
    assert settlement_price("call", 500.0, 503.25) == 3.25
    assert settlement_price("put", 500.0, 497.0) == 3.0
    assert settlement_price("call", 500.0, 499.0) == 0.0     # OTM -> worthless
    assert settlement_price("put", 500.0, None) == 0.0       # unknown -> conservative


def test_buy_refuses_what_the_balance_cannot_cover(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    storage.ensure_account(path, 300.0)
    assert buy(path, "QQQ", "call", 450.0, "2026-07-07", 2.0, 2, 0.5) is None  # $400
    assert storage.get_open_positions(path) == []
    assert storage.get_balance(path) == 300.0


def test_buy_and_close_never_read_modify_write_the_balance(tmp_path, monkeypatch):
    # set_balance(get_balance() +/- x) across two connections is a lost update
    # waiting for the worker and the API to close at the same moment
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    storage.ensure_account(path, 10000.0)
    monkeypatch.setattr(storage, "set_balance",
                        lambda *a, **k: pytest.fail("balance must be updated relatively"))
    pid = buy(path, "QQQ", "call", 450.0, "2026-07-07", 2.0, 1, 0.5)
    assert close(path, pid, 3.0, "manual") == 100.0
    assert storage.get_balance(path) == 10100.0
