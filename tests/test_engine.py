import pytest

from paper_trading.engine import calculate_contracts, calculate_pnl, evaluate_exit
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
    reason = evaluate_exit(position, current_price=3.5, current_composite_score=0.7,
                            minutes_to_close=120, exit_rules=EXIT_RULES)
    assert reason == "profit_target"


def test_evaluate_exit_stop_loss():
    position = make_position(entry_price=2.0)
    reason = evaluate_exit(position, current_price=1.0, current_composite_score=0.7,
                            minutes_to_close=120, exit_rules=EXIT_RULES)
    assert reason == "stop_loss"


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
