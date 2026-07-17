"""Walk-forward validation.

The two tests that matter: it must REJECT a rule fitted to pure noise (the
failure mode that produced a +141% mirage on real data), and it must still
ACCEPT a genuinely predictive rule. A validator that only ever says "no" is
decoration, not a check.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from analytics.walkforward import (
    build_rule_grid, chronological_split, evaluate_rule, noise_floor_t,
    walk_forward_search,
)

_T0 = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)


def _row(i, signal_value, fwd, name="technicals"):
    return {"timestamp": _T0 + timedelta(minutes=30 * i),
            "subscores": {name: signal_value}, "fwd": fwd}


# --- split -------------------------------------------------------------------

def test_chronological_split_is_by_time_not_position():
    rows = [_row(i, 0.5, 1.0) for i in range(10)]
    random.Random(1).shuffle(rows)          # arrives out of order
    train, test = chronological_split(rows, 0.7)
    assert len(train) == 7 and len(test) == 3
    # every train row must predate every test row
    assert max(r["timestamp"] for r in train) < min(r["timestamp"] for r in test)


def test_chronological_split_empty():
    assert chronological_split([], 0.7) == ([], [])


# --- rule evaluation ---------------------------------------------------------

def test_evaluate_rule_longs_shorts_and_stands_aside():
    rows = [
        _row(0, 0.5, 2.0),    # above +0.3 -> long, captures +2.0
        _row(1, -0.5, -3.0),  # below -0.3 -> short, captures +3.0
        _row(2, 0.1, 99.0),   # inside the band -> no trade
    ]
    r = evaluate_rule({"signal": "technicals", "threshold": 0.3, "sign": 1}, rows)
    assert r["trades"] == 2
    assert r["total_pct"] == pytest.approx(5.0)


def test_evaluate_rule_sign_inverts_the_rule():
    rows = [_row(0, 0.5, 2.0), _row(1, 0.5, 2.0)]
    long_r = evaluate_rule({"signal": "technicals", "threshold": 0.3, "sign": 1}, rows)
    fade_r = evaluate_rule({"signal": "technicals", "threshold": 0.3, "sign": -1}, rows)
    assert long_r["total_pct"] == pytest.approx(4.0)
    assert fade_r["total_pct"] == pytest.approx(-4.0)   # same rows, faded


def test_evaluate_rule_ignores_missing_signal_or_return():
    rows = [_row(0, None, 2.0), {"timestamp": _T0, "subscores": {}, "fwd": 1.0},
            _row(2, 0.5, None)]
    assert evaluate_rule({"signal": "technicals", "threshold": 0.1, "sign": 1},
                         rows)["trades"] == 0


# --- the noise floor ---------------------------------------------------------

def test_noise_floor_grows_with_the_number_of_rules_searched():
    assert noise_floor_t(1) == 0.0
    # searching more rules raises the bar a winner must clear
    assert noise_floor_t(400) > noise_floor_t(10) > noise_floor_t(2)
    assert noise_floor_t(400) == pytest.approx(3.46, abs=0.05)


def test_build_rule_grid_size_is_explicit():
    grid = build_rule_grid(["a", "b"], [0.1, 0.2, 0.3])
    assert len(grid) == 2 * 3 * 2   # signals x thresholds x both signs


# --- THE tests that matter ---------------------------------------------------

def test_walk_forward_REJECTS_a_rule_fitted_to_pure_noise():
    """The +141% mirage, reproduced: random signal, random returns, no relation.
    A big search WILL find a great in-sample rule. The validator must kill it."""
    rng = random.Random(7)
    rows = [_row(i, rng.uniform(-1, 1), rng.gauss(0, 1)) for i in range(600)]
    rules = build_rule_grid(["technicals"], [0.05, 0.1, 0.2, 0.3, 0.4, 0.5])
    result = walk_forward_search(rules, rows, train_frac=0.7, min_trades=20)

    # the search still finds a "winner" in-sample - that's the whole point
    assert result["train"]["total_pct"] > 0
    assert result["survived"] is False
    assert "REJECTED" in result["verdict"]


def test_walk_forward_ACCEPTS_a_genuinely_predictive_rule():
    """A real (planted) edge: the signal actually predicts the return, in both
    halves. The validator must not reject this, or it's useless."""
    rng = random.Random(11)
    rows = []
    for i in range(600):
        v = rng.uniform(-1, 1)
        # forward return follows the signal, plus honest noise
        rows.append(_row(i, v, v * 2.0 + rng.gauss(0, 0.4)))
    rules = build_rule_grid(["technicals"], [0.1, 0.2, 0.3])
    result = walk_forward_search(rules, rows, train_frac=0.7, min_trades=20)

    assert result["survived"] is True
    assert "SURVIVED" in result["verdict"]
    assert result["test"]["total_pct"] > 0
    # a real edge keeps working: its in-sample winners survive out of sample
    assert result["top_decile_survivors"] == result["top_decile_n"]


def test_walk_forward_reports_the_noise_floor_and_counts():
    rng = random.Random(3)
    rows = [_row(i, rng.uniform(-1, 1), rng.gauss(0, 1)) for i in range(400)]
    rules = build_rule_grid(["technicals"], [0.1, 0.2, 0.3])
    result = walk_forward_search(rules, rows, min_trades=10)
    assert result["rules_tested"] > 0
    assert result["noise_floor_t"] > 0
    assert result["train_rows"] + result["test_rows"] == 400


def test_walk_forward_handles_too_little_data():
    assert "error" in walk_forward_search(build_rule_grid(["x"], [0.1]), [])
    rows = [_row(i, 0.5, 1.0) for i in range(10)]
    assert "error" in walk_forward_search(build_rule_grid(["technicals"], [0.1]),
                                          rows, min_trades=999)


# --- the overlapping-samples trap (the validator's own near-miss) -------------

def test_evaluate_rule_t_stat_uses_INDEPENDENT_trades_not_raw_count():
    """Regression: trades from 5-min bars graded on a 30-min window overlap ~6x.
    Counting them as independent inflates every t-stat by ~sqrt(6) - the same
    error that let trump_news collect half a ticker's weight. On its first real
    run this made an IWM rule look like it 'SURVIVED' out of sample."""
    # 60 rows, 5 min apart, all triggering a trade, graded on a 30-min horizon
    rows = [{"timestamp": _T0 + timedelta(minutes=5 * i),
             "subscores": {"technicals": 0.5}, "fwd": 1.0} for i in range(60)]
    r = evaluate_rule({"signal": "technicals", "threshold": 0.1, "sign": 1},
                      rows, horizon_minutes=30)
    assert r["trades"] == 60                # raw count
    assert r["independent_trades"] == 10    # 60 bars * 5 min / 30 min window
    assert r["independent_trades"] < r["trades"]


def test_independent_trades_equal_raw_when_windows_dont_overlap():
    rows = [{"timestamp": _T0 + timedelta(minutes=30 * i),
             "subscores": {"technicals": 0.5}, "fwd": 1.0} for i in range(10)]
    r = evaluate_rule({"signal": "technicals", "threshold": 0.1, "sign": 1},
                      rows, horizon_minutes=30)
    assert r["independent_trades"] == r["trades"] == 10


def test_overlap_correction_shrinks_the_t_stat():
    """Same returns, same rule - only the bar spacing differs. The densely
    sampled version must NOT look more significant just for being sampled more."""
    rng = random.Random(5)
    rets = [rng.gauss(0.05, 1.0) for _ in range(120)]
    dense = [{"timestamp": _T0 + timedelta(minutes=5 * i),
              "subscores": {"technicals": 0.5}, "fwd": r} for i, r in enumerate(rets)]
    sparse = [{"timestamp": _T0 + timedelta(minutes=30 * i),
               "subscores": {"technicals": 0.5}, "fwd": r} for i, r in enumerate(rets)]
    rule = {"signal": "technicals", "threshold": 0.1, "sign": 1}
    t_dense = abs(evaluate_rule(rule, dense, 30)["t_stat"])
    t_sparse = abs(evaluate_rule(rule, sparse, 30)["t_stat"])
    assert t_dense < t_sparse   # oversampling buys no significance
