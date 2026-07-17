"""Event study.

The two that matter: a PLANTED market mover must be detected and distinguished
from the control, and pure noise must NOT be - otherwise the tool just launders
narrative fallacy ("Trump said X and QQQ took off!") into something that looks
like evidence.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from analytics.events import (
    detect_price_shocks, detect_signal_shocks, event_study, forward_path,
)

_T0 = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)


def _h(minute, spot, subs=None, direction="neutral"):
    return {"timestamp": _T0 + timedelta(minutes=minute), "spot_price": spot,
            "direction": direction, "composite_score": 0.0, "subscores": subs or {}}


# --- shock detection ---------------------------------------------------------

def test_detect_signal_shocks_finds_the_lurch():
    hist = [_h(0, 100, {"trump_news": 0.1}), _h(1, 100, {"trump_news": 0.1}),
            _h(2, 100, {"trump_news": 0.8}),   # +0.7 lurch
            _h(3, 100, {"trump_news": 0.8})]
    shocks = detect_signal_shocks(hist, "trump_news", min_delta=0.3)
    assert len(shocks) == 1
    assert shocks[0]["delta"] == pytest.approx(0.7)
    assert shocks[0]["from"] == 0.1 and shocks[0]["to"] == 0.8


def test_detect_signal_shocks_ignores_small_drift():
    hist = [_h(i, 100, {"trump_news": 0.1 + i * 0.02}) for i in range(10)]
    assert detect_signal_shocks(hist, "trump_news", min_delta=0.3) == []


def test_detect_signal_shocks_dedupes_one_event_across_cycles():
    """One piece of news often nudges the score over several cycles. Counting
    each nudge would triple-count a single event and fake the sample size."""
    hist = [_h(0, 100, {"trump_news": 0.0}),
            _h(1, 100, {"trump_news": 0.4}),    # shock
            _h(2, 100, {"trump_news": 0.8}),    # same event, 1 min later
            _h(3, 100, {"trump_news": 1.0})]    # still the same event
    shocks = detect_signal_shocks(hist, "trump_news", min_delta=0.3,
                                  min_gap_minutes=30)
    assert len(shocks) == 1          # not 3
    # a genuinely separate event, well after the gap, does count
    hist.append(_h(60, 100, {"trump_news": 0.2}))
    hist.append(_h(61, 100, {"trump_news": 0.9}))
    assert len(detect_signal_shocks(hist, "trump_news", 0.3, 30)) == 2


def test_detect_price_shocks_finds_the_moves_that_mattered():
    hist = [_h(0, 100.0), _h(30, 100.6), _h(60, 100.6)]   # +0.6% in 30 min
    shocks = detect_price_shocks(hist, min_move_pct=0.4, window_minutes=30)
    assert len(shocks) == 1
    assert shocks[0]["move_pct"] == pytest.approx(0.6, abs=0.01)


# --- forward path ------------------------------------------------------------

def test_forward_path_measures_from_the_event():
    hist = [_h(0, 100.0), _h(5, 101.0), _h(30, 103.0)]
    path = forward_path(hist, _T0, offsets=(5, 30))
    assert path[5] == pytest.approx(1.0)
    assert path[30] == pytest.approx(3.0)


def test_forward_path_none_when_history_runs_out():
    hist = [_h(0, 100.0), _h(5, 101.0)]
    assert forward_path(hist, _T0, offsets=(5, 240))[240] is None


# --- THE tests that matter ---------------------------------------------------

def _noise_history(n=400, seed=3):
    rng = random.Random(seed)
    spot, hist = 100.0, []
    for i in range(n):
        spot *= 1 + rng.gauss(0, 0.001)
        # a signal that lurches at random, unrelated to price
        val = 0.8 if (i % 60 == 0 and i) else 0.1
        hist.append(_h(i, spot, {"trump_news": val}))
    return hist


def test_event_study_REJECTS_a_signal_unrelated_to_price():
    """Narrative-fallacy guard: the signal lurches, price does its own thing.
    The tool must say nothing happened."""
    hist = _noise_history()
    shocks = detect_signal_shocks(hist, "trump_news", min_delta=0.3)
    assert shocks  # events exist...
    result = event_study(hist, shocks, offsets=(5, 30, 60), control_samples=100)
    assert result["strongest_t"] < 2
    assert "indistinguishable" in result["verdict"]


def test_event_study_DETECTS_a_planted_market_mover():
    """A real mover: every time the signal lurches, price runs the same way.
    The tool must separate it from the control, or it's useless."""
    rng = random.Random(5)
    spot, hist = 100.0, []
    for i in range(400):
        shock = (i % 60 == 0 and i > 0)
        if shock:
            spot *= 1.004          # the event genuinely drives price up
        else:
            spot *= 1 + rng.gauss(0, 0.0003)
        hist.append(_h(i, spot, {"trump_news": 0.8 if shock else 0.1}))
    shocks = detect_signal_shocks(hist, "trump_news", min_delta=0.3)
    result = event_study(hist, shocks, offsets=(5, 30), control_samples=150)
    assert result["events"] >= 3
    # price after a real mover must beat a random moment
    assert result["event_path"][5] > result["control_path"][5]


def test_event_study_signed_flips_negative_events():
    """A bearish lurch followed by a fall is a HIT, same as bullish->rise.
    Without signing, the two cancel and a real signal looks like nothing."""
    hist = [_h(0, 100.0, {"trump_news": 0.8}), _h(5, 99.0, {"trump_news": 0.8})]
    down = [{"timestamp": _T0, "delta": -0.7}]
    signed = event_study(hist, down, offsets=(5,), control_samples=0, signed=True)
    unsigned = event_study(hist, down, offsets=(5,), control_samples=0, signed=False)
    assert signed["event_path"][5] == pytest.approx(1.0)     # fall after bearish = +1
    assert unsigned["event_path"][5] == pytest.approx(-1.0)


def test_event_study_no_events():
    assert event_study([], [])["events"] == 0
