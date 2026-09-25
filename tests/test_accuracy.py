from datetime import datetime, timedelta, timezone

import pytest

from analytics import accuracy


def _snap(minutes_offset: int, direction: str, spot_price: float | None):
    return {
        "timestamp": datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset),
        "direction": direction,
        "spot_price": spot_price,
    }


def test_evaluate_signal_accuracy_marks_hit_when_price_moves_as_predicted():
    snapshots = [
        _snap(0, "bullish", 100.0),
        _snap(30, "bullish", 101.0),
    ]
    results = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    assert results[0]["evaluated"] is True
    assert results[0]["hit"] is True


def test_evaluate_signal_accuracy_marks_miss_when_price_moves_against_prediction():
    snapshots = [
        _snap(0, "bullish", 100.0),
        _snap(30, "bullish", 99.0),
    ]
    results = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    assert results[0]["hit"] is False


def test_evaluate_signal_accuracy_bearish_direction():
    snapshots = [
        _snap(0, "bearish", 100.0),
        _snap(30, "bearish", 98.0),
    ]
    results = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    assert results[0]["hit"] is True


def test_evaluate_signal_accuracy_not_evaluated_when_no_future_snapshot_far_enough():
    snapshots = [
        _snap(0, "bullish", 100.0),
        _snap(10, "bullish", 101.0),  # only 10 min later, horizon is 30
    ]
    results = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    assert results[0]["evaluated"] is False
    assert results[0]["hit"] is None


def test_evaluate_signal_accuracy_skips_neutral_signals():
    snapshots = [
        _snap(0, "neutral", 100.0),
        _snap(30, "neutral", 105.0),
    ]
    results = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    assert results[0]["evaluated"] is False


def test_evaluate_signal_accuracy_skips_when_spot_price_missing():
    snapshots = [
        _snap(0, "bullish", None),
        _snap(30, "bullish", 101.0),
    ]
    results = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes=30)
    assert results[0]["evaluated"] is False


def test_daily_accuracy_summary_groups_and_computes_hit_rate():
    evaluated = [
        {"timestamp": datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc), "evaluated": True, "hit": True},
        {"timestamp": datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc), "evaluated": True, "hit": False},
        {"timestamp": datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc), "evaluated": False, "hit": None},
    ]
    summary = accuracy.daily_accuracy_summary(evaluated, tz_name="UTC")
    assert len(summary) == 1
    assert summary[0]["total"] == 2
    assert summary[0]["hits"] == 1
    assert summary[0]["accuracy_pct"] == 50.0


def test_daily_accuracy_summary_empty_when_nothing_evaluated():
    assert accuracy.daily_accuracy_summary([]) == []


def test_overall_accuracy_pct_computes_ratio():
    evaluated = [
        {"evaluated": True, "hit": True},
        {"evaluated": True, "hit": True},
        {"evaluated": True, "hit": False},
        {"evaluated": False, "hit": None},
    ]
    assert accuracy.overall_accuracy_pct(evaluated) == 2 / 3 * 100.0


def test_overall_accuracy_pct_none_when_nothing_evaluated():
    assert accuracy.overall_accuracy_pct([]) is None


def _history_snap(minutes_offset: int, spot_price: float, subscores: dict):
    return {
        "timestamp": datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset),
        "spot_price": spot_price,
        "subscores": subscores,
    }


def test_evaluate_category_accuracy_separates_good_and_bad_signals():
    # steady uptrend: a bullish-calling signal should score well, a bearish-calling
    # signal on the same data should score poorly.
    history = [
        _history_snap(0, 100.0, {"good_signal": 0.8, "bad_signal": -0.8}),
        _history_snap(30, 101.0, {"good_signal": 0.8, "bad_signal": -0.8}),
        _history_snap(60, 102.0, {"good_signal": 0.8, "bad_signal": -0.8}),
        _history_snap(90, 103.0, {"good_signal": 0.8, "bad_signal": -0.8}),
    ]
    results = accuracy.evaluate_category_accuracy(history, horizon_minutes=30)

    assert results["good_signal"]["accuracy_pct"] == 100.0
    assert results["bad_signal"]["accuracy_pct"] == 0.0
    assert results["good_signal"]["graded_count"] == 3
    assert results["bad_signal"]["graded_count"] == 3


def test_evaluate_category_accuracy_treats_missing_category_as_ungraded():
    history = [
        _history_snap(0, 100.0, {"sometimes_present": 0.8}),
        _history_snap(30, 101.0, {}),  # category absent this cycle
        _history_snap(60, 102.0, {"sometimes_present": 0.8}),
    ]
    results = accuracy.evaluate_category_accuracy(history, horizon_minutes=30)

    # only the t=0 reading has a later snapshot >=30 min out with the category present
    # to grade against (t=30's absence doesn't count as a wrong call)
    assert results["sometimes_present"]["graded_count"] == 1
    assert results["sometimes_present"]["accuracy_pct"] == 100.0


def test_evaluate_category_accuracy_empty_history():
    assert accuracy.evaluate_category_accuracy([], horizon_minutes=30) == {}


def test_suggest_weights_favors_more_accurate_category():
    category_accuracy = {
        "good_signal": {"accuracy_pct": 80.0, "graded_count": 20},
        "bad_signal": {"accuracy_pct": 55.0, "graded_count": 20},
    }
    current_weights = {"good_signal": 0.5, "bad_signal": 0.5}

    suggested = accuracy.suggest_weights(category_accuracy, current_weights)

    assert suggested["good_signal"] > suggested["bad_signal"]
    # total weight-mass across the reallocated categories is preserved
    assert suggested["good_signal"] + suggested["bad_signal"] == pytest.approx(1.0)


def test_suggest_weights_leaves_undersampled_categories_untouched():
    category_accuracy = {
        "proven_signal": {"accuracy_pct": 80.0, "graded_count": 20},
        "new_signal": {"accuracy_pct": 90.0, "graded_count": 2},  # below min_graded
    }
    current_weights = {"proven_signal": 0.5, "new_signal": 0.5}

    suggested = accuracy.suggest_weights(category_accuracy, current_weights, min_graded=10)

    # new_signal isn't reweighted despite its high accuracy - not enough samples yet
    assert suggested["new_signal"] == 0.5
    # proven_signal is the only eligible category, so it keeps its full share
    assert suggested["proven_signal"] == pytest.approx(0.5)


def test_suggest_weights_none_when_nothing_has_enough_data():
    category_accuracy = {"signal_a": {"accuracy_pct": 90.0, "graded_count": 3}}
    assert accuracy.suggest_weights(category_accuracy, {"signal_a": 1.0}, min_graded=10) is None


def test_confidence_calibration_buckets_by_predicted_confidence():
    evaluated = [
        {"evaluated": True, "hit": True, "confidence": 10.0},
        {"evaluated": True, "hit": False, "confidence": 15.0},
        {"evaluated": True, "hit": True, "confidence": 65.0},
        {"evaluated": True, "hit": True, "confidence": 70.0},
        {"evaluated": False, "hit": None, "confidence": 50.0},  # ungraded, excluded
    ]
    calibration = accuracy.confidence_calibration(evaluated)

    assert len(calibration) == 2  # empty bands omitted
    low_band = next(b for b in calibration if b["band"] == "0-20%")
    high_band = next(b for b in calibration if b["band"] == "60-100%")
    assert low_band["count"] == 2
    assert low_band["observed_accuracy_pct"] == 50.0
    assert high_band["count"] == 2
    assert high_band["observed_accuracy_pct"] == 100.0


def test_confidence_calibration_empty_when_nothing_graded():
    assert accuracy.confidence_calibration([]) == []


def test_inversion_candidates_flags_reliably_wrong_categories():
    category_accuracy = {
        "wrong_signal": {"accuracy_pct": 30.0, "graded_count": 20},
        "good_signal": {"accuracy_pct": 70.0, "graded_count": 20},
        "wrong_but_undersampled": {"accuracy_pct": 10.0, "graded_count": 3},
    }
    assert accuracy.inversion_candidates(category_accuracy, min_graded=10) == ["wrong_signal"]


def test_inversion_candidates_empty_when_all_fine():
    category_accuracy = {"signal_a": {"accuracy_pct": 55.0, "graded_count": 50}}
    assert accuracy.inversion_candidates(category_accuracy, min_graded=10) == []


# --- automatic self-calibration ---------------------------------------------

from datetime import date


def test_blend_weights_moves_fraction_toward_suggestion():
    current = {"a": 0.5, "b": 0.5}
    suggested = {"a": 0.9, "b": 0.1}
    blended = accuracy.blend_weights(current, suggested, 0.25)
    assert blended["a"] == pytest.approx(0.6)   # 0.5 + 0.25*(0.9-0.5)
    assert blended["b"] == pytest.approx(0.4)


def test_blend_weights_noop_when_equal():
    w = {"a": 0.3, "b": 0.7}
    assert accuracy.blend_weights(w, dict(w), 0.25) == pytest.approx(w)


def test_blend_weights_missing_category_keeps_current():
    current = {"a": 0.4, "b": 0.6}
    suggested = {"a": 0.8}  # b absent
    blended = accuracy.blend_weights(current, suggested, 0.5)
    assert blended["a"] == pytest.approx(0.6)
    assert blended["b"] == pytest.approx(0.6)  # unchanged


def test_calibrated_confidence_band_hit():
    bands = [{"lo": 60, "hi": 101, "count": 20, "observed_accuracy_pct": 72.0}]
    assert accuracy.calibrated_confidence(65.0, bands, min_band_count=5) == 72.0


def test_calibrated_confidence_low_count_falls_back_to_raw():
    bands = [{"lo": 60, "hi": 101, "count": 3, "observed_accuracy_pct": 72.0}]
    assert accuracy.calibrated_confidence(65.0, bands, min_band_count=5) == 65.0


def test_calibrated_confidence_out_of_band_falls_back():
    bands = [{"lo": 60, "hi": 101, "count": 20, "observed_accuracy_pct": 72.0}]
    assert accuracy.calibrated_confidence(30.0, bands, min_band_count=5) == 30.0


def test_calibrated_confidence_no_map_returns_raw():
    assert accuracy.calibrated_confidence(55.0, None) == 55.0
    assert accuracy.calibrated_confidence(55.0, []) == 55.0


def test_calibrated_confidence_clamps():
    bands = [{"lo": 0, "hi": 101, "count": 10, "observed_accuracy_pct": 150.0}]
    assert accuracy.calibrated_confidence(50.0, bands) == 100.0


class _Row(dict):
    """dict that also supports row['x'] like sqlite3.Row already does; here just
    a plain dict is enough since history_snapshots uses subscript access."""


def test_history_snapshots_converts_rows():
    rows = [
        _Row(timestamp="2026-07-06T14:00:00+00:00", direction="bullish",
             spot_price=100.0, confidence=60.0, composite_score=0.6,
             subscores_json='{"technicals": 0.5}'),
    ]
    snaps = accuracy.history_snapshots(rows)
    assert snaps[0]["direction"] == "bullish"
    assert snaps[0]["spot_price"] == 100.0
    assert snaps[0]["subscores"] == {"technicals": 0.5}
    assert snaps[0]["timestamp"].year == 2026


def _cal_cfg(**overrides):
    cfg = {
        "learning_rate": 0.25, "inversion_cooldown_days": 5,
        "confidence_min_graded": 20, "weight_min_graded": 3,
        "inversion_min_graded": 3, "inversion_max_accuracy_pct": 40.0,
        "horizon_minutes": 30,
    }
    cfg.update(overrides)
    return cfg


def _history_for_category(direction_correct: bool, n: int, category: str):
    """Snapshots 30 min apart whose call ALTERNATES bullish/bearish, with a price
    path that makes every graded call consistently right or wrong.

    Alternating matters: a constantly-bullish run is one independent observation
    no matter how many minutes it spans (see independent_observations), so a
    constant-direction fixture can never clear a trust gate. Each snapshot here
    is a distinct call (its own direction run) landing in its own non-overlapping
    30-min window, so n snapshots really are ~n independent observations."""
    base = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        bullish = i % 2 == 0
        # correct: bullish@200 -> 201 (up, hit), bearish@201 -> 200 (down, hit)
        # wrong:   bullish@200 -> 199 (down, miss), bearish@199 -> 200 (up, miss)
        if direction_correct:
            spot = 200.0 if bullish else 201.0
        else:
            spot = 200.0 if bullish else 199.0
        score = 0.5 if bullish else -0.5
        rows.append({
            "timestamp": base + timedelta(minutes=30 * i),
            "spot_price": spot,
            "direction": "bullish" if bullish else "bearish",
            "confidence": 50.0,
            "composite_score": score,
            "subscores": {category: score},
        })
    return rows


def test_plan_calibration_adds_inversion_for_wrong_category():
    history = _history_for_category(direction_correct=False, n=6, category="sentiment")
    actions = accuracy.plan_calibration(
        "QQQ", history, {"sentiment": 1.0}, [], [], _cal_cfg(), date(2026, 7, 7),
    )
    inverts = [a for a in actions if a["kind"] == "invert"]
    assert any(a["category"] == "sentiment" for a in inverts)


def test_plan_calibration_respects_inversion_cooldown():
    history = _history_for_category(direction_correct=False, n=6, category="sentiment")
    recent = [{"kind": "inversion_added",
               "detail": {"category": "sentiment"},
               "created_at": "2026-07-05T20:00:00+00:00"}]  # 2 days ago < 5
    actions = accuracy.plan_calibration(
        "QQQ", history, {"sentiment": 1.0}, [], recent, _cal_cfg(), date(2026, 7, 7),
    )
    assert not any(a["kind"] == "invert" for a in actions)


def test_plan_calibration_confidence_map_needs_enough_graded():
    history = _history_for_category(direction_correct=True, n=6, category="technicals")
    # only 6 graded < confidence_min_graded=20 -> no confidence_map action
    actions = accuracy.plan_calibration(
        "QQQ", history, {"technicals": 1.0}, [], [], _cal_cfg(), date(2026, 7, 7),
    )
    assert not any(a["kind"] == "confidence_map" for a in actions)


def test_plan_calibration_stores_confidence_map_with_enough_graded():
    history = _history_for_category(direction_correct=True, n=25, category="technicals")
    actions = accuracy.plan_calibration(
        "QQQ", history, {"technicals": 1.0}, [], [],
        _cal_cfg(confidence_min_graded=20), date(2026, 7, 7),
    )
    assert any(a["kind"] == "confidence_map" for a in actions)


# --- context-conditional accuracy -------------------------------------------

def _ctx_snap(hour, direction, spot, vol_regime=None, minute=0):
    subs = {"technicals": 0.5}
    if vol_regime is not None:
        subs["volatility_regime"] = vol_regime
    return {
        "timestamp": datetime(2026, 7, 6, hour, minute, tzinfo=timezone.utc),
        "direction": direction, "spot_price": spot, "confidence": 50.0,
        "composite_score": 0.5, "subscores": subs,
    }


def test_context_time_of_day_buckets():
    # 14:00 UTC = 10:00 ET (morning), 17:00 UTC = 13:00 ET (midday), 20:00 UTC = 16:00 ET (afternoon)
    assert accuracy.context_time_of_day(_ctx_snap(14, "bullish", 100)) == "morning"
    assert accuracy.context_time_of_day(_ctx_snap(17, "bullish", 100)) == "midday"
    assert accuracy.context_time_of_day(_ctx_snap(20, "bullish", 100)) == "afternoon"


def test_context_volatility_regime_from_subscore():
    assert accuracy.context_volatility_regime(_ctx_snap(14, "bullish", 100, vol_regime=0.3)) == "calm"
    assert accuracy.context_volatility_regime(_ctx_snap(14, "bullish", 100, vol_regime=-0.3)) == "stressed"
    assert accuracy.context_volatility_regime(_ctx_snap(14, "bullish", 100)) is None


def test_bucket_evaluated_splits_by_context():
    # morning call right, afternoon call wrong; grade at 30min then bucket
    history = [
        _ctx_snap(14, "bullish", 100.0, minute=0),    # 10:00 ET (morning), graded call
        _ctx_snap(14, "neutral", 101.0, minute=30),   # +30min grading point (neutral -> not itself graded)
        _ctx_snap(20, "bullish", 100.0, minute=0),    # 16:00 ET (afternoon), graded call
        _ctx_snap(20, "neutral", 99.0, minute=30),    # +30min grading point
    ]
    evaluated = accuracy.evaluate_signal_accuracy(history, horizon_minutes=30)
    buckets = accuracy.bucket_evaluated(evaluated, accuracy.context_time_of_day)
    assert buckets["morning"]["accuracy_pct"] == 100.0
    assert buckets["afternoon"]["accuracy_pct"] == 0.0


def test_accuracy_by_context_per_category():
    history = [
        _ctx_snap(14, "bullish", 100.0, vol_regime=0.5, minute=0),
        _ctx_snap(14, "bullish", 101.0, vol_regime=0.5, minute=30),
    ]
    result = accuracy.accuracy_by_context(
        history, 30, accuracy.context_volatility_regime, category="technicals")
    assert result["calm"]["graded"] == 1
    assert result["calm"]["accuracy_pct"] == 100.0


# --- effective sample size ---------------------------------------------------

def _graded(minute, direction="bullish", hit=True, confidence=50.0):
    return {"timestamp": datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
            "direction": direction, "evaluated": True, "hit": hit, "confidence": confidence}


def test_non_overlapping_count_collapses_contiguous_minutes():
    # THE regression test: 30 contiguous 1-min snapshots graded at a 30-min
    # horizon are ONE independent observation, not 30.
    snaps = [_graded(m) for m in range(30)]
    assert accuracy.non_overlapping_count(snaps, horizon_minutes=30) == 1
    # 390 minutes (a full session) at a 30-min horizon -> 13
    session = [_graded(m) for m in range(390)]
    assert accuracy.non_overlapping_count(session, horizon_minutes=30) == 13


def test_non_overlapping_count_keeps_well_spaced_samples():
    snaps = [_graded(0), _graded(60), _graded(120)]
    assert accuracy.non_overlapping_count(snaps, horizon_minutes=30) == 3


def test_non_overlapping_count_ignores_ungraded():
    snaps = [_graded(0), dict(_graded(60), evaluated=False)]
    assert accuracy.non_overlapping_count(snaps, horizon_minutes=30) == 1


def test_direction_run_count():
    # constant direction all session = ONE call, not 390
    assert accuracy.direction_run_count([_graded(m, "bullish") for m in range(390)]) == 1
    # alternating every sample = a distinct call each time
    alt = [_graded(m, "bullish" if m % 2 == 0 else "bearish") for m in range(6)]
    assert accuracy.direction_run_count(alt) == 6
    # two runs
    two = [_graded(0, "bullish"), _graded(1, "bullish"), _graded(2, "bearish")]
    assert accuracy.direction_run_count(two) == 2
    assert accuracy.direction_run_count([]) == 0


def test_independent_observations_takes_the_stricter_counter():
    # a slow signal: constant direction across a full session.
    # windows say 13, runs say 1 -> the honest answer is 1.
    slow = [_graded(m, "bullish") for m in range(390)]
    assert accuracy.non_overlapping_count(slow, 30) == 13
    assert accuracy.direction_run_count(slow) == 1
    assert accuracy.independent_observations(slow, 30) == 1

    # a fast signal: flips every minute across a session.
    # runs say 390, windows say 13 -> the honest answer is 13.
    fast = [_graded(m, "bullish" if m % 2 == 0 else "bearish") for m in range(390)]
    assert accuracy.independent_observations(fast, 30) == 13


def test_gates_use_independent_count_not_raw_minutes():
    # 600 raw graded minutes but only 3 independent observations
    thin = {"slow_signal": {"accuracy_pct": 20.0, "graded_count": 600, "independent_count": 3}}
    assert accuracy.suggest_weights(thin, {"slow_signal": 1.0}, min_graded=10) is None
    assert accuracy.inversion_candidates(thin, min_graded=10) == []
    # same category with genuinely independent samples clears the gate
    thick = {"slow_signal": {"accuracy_pct": 20.0, "graded_count": 600, "independent_count": 40}}
    assert accuracy.inversion_candidates(thick, min_graded=10) == ["slow_signal"]


def test_gates_fall_back_to_graded_count_when_independent_absent():
    legacy = {"cat": {"accuracy_pct": 20.0, "graded_count": 40}}
    assert accuracy.inversion_candidates(legacy, min_graded=10) == ["cat"]


def test_calibrated_confidence_distrusts_a_thin_band():
    # exactly the NVDA case: 14 raw samples in the 60-100 band, ~1 real one
    band = [{"lo": 60, "hi": 101, "count": 14, "independent_count": 1,
             "observed_accuracy_pct": 7.1}]
    assert accuracy.calibrated_confidence(65.0, band, min_band_count=5) == 65.0  # falls back to raw
    fat = [{"lo": 60, "hi": 101, "count": 600, "independent_count": 20,
            "observed_accuracy_pct": 7.1}]
    assert accuracy.calibrated_confidence(65.0, fat, min_band_count=5) == 7.1


def test_confidence_map_reports_both_counts():
    snaps = [_graded(m, confidence=65.0) for m in range(60)]
    bands = accuracy.confidence_map(snaps, horizon_minutes=30)
    band = next(b for b in bands if b["lo"] == 60)
    assert band["count"] == 60          # every graded minute
    assert band["independent_count"] == 2   # 60 min / 30 min horizon


def test_context_direction_streak_buckets():
    assert accuracy.context_direction_streak({"direction_streak": 1}) == "fresh (1-3)"
    assert accuracy.context_direction_streak({"direction_streak": 3}) == "fresh (1-3)"
    assert accuracy.context_direction_streak({"direction_streak": 4}) == "building (4-15)"
    assert accuracy.context_direction_streak({"direction_streak": 15}) == "building (4-15)"
    assert accuracy.context_direction_streak({"direction_streak": 16}) == "sustained (16+)"
    # snapshots from before streak tracking are excluded, not bucketed as fresh
    assert accuracy.context_direction_streak({"direction_streak": None}) is None
    assert accuracy.context_direction_streak({}) is None


# --- look-back window + current-composite cutoff -------------------------------

def test_grading_takes_the_first_later_price_past_the_horizon():
    # equal timestamps, a missing price at the target, and nothing gradeable
    # at the end - the bisection must pick exactly what the linear scan did
    snaps = [_snap(0, "bullish", 100.0), _snap(0, "bearish", 100.0),
             _snap(30, "bullish", None), _snap(31, "neutral", 101.0),
             _snap(45, "bullish", 99.0)]
    graded = accuracy.evaluate_signal_accuracy(snaps, horizon_minutes=30)
    assert [g["future_price"] for g in graded] == [101.0, 101.0, None, None, None]
    assert [g["hit"] for g in graded] == [True, False, None, None, None]


def test_history_window_start_is_a_time_window():
    now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    assert accuracy.history_window_start({"accuracy_window_days": 10}, now) == \
        datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
    assert accuracy.history_window_start({}, now) == now - timedelta(days=30)


def test_composite_start_is_market_midnight_of_the_configured_date():
    config = {"composite_since": "2026-09-17",
              "market_hours": {"timezone": "America/New_York"}}
    start = accuracy.composite_start(config)
    assert start.isoformat() == "2026-09-17T00:00:00-04:00"
    assert accuracy.composite_start({}) is None


def test_since_composite_drops_calls_from_an_older_composite():
    history = [_snap(0, "bullish", 100.0), _snap(60, "bullish", 100.0)]
    cutoff = history[1]["timestamp"]
    assert accuracy.since_composite(history, cutoff) == [history[1]]
    assert accuracy.since_composite(history, None) == history


def test_plan_calibration_only_weighs_live_signals():
    # a removed signal still sitting in the window with a great record must
    # not be handed a share of the live signals' weight
    good_dead = _history_for_category(direction_correct=True, n=8, category="trump_news")
    for row in good_dead:
        row["subscores"]["technicals"] = row["subscores"]["trump_news"] * -1  # always wrong
    actions = accuracy.plan_calibration(
        "QQQ", good_dead, {"technicals": 1.0}, [], [], _cal_cfg(), date(2026, 7, 7))
    # with the dead signal graded, technicals' weight was pushed toward ~1% of
    # its own share; with only live signals there is nothing to reallocate
    assert not any(action["kind"] == "weights" for action in actions)
    assert all(action["category"] == "technicals" for action in actions
               if action["kind"] in ("invert", "uninvert"))


def test_plan_calibration_confidence_map_ignores_an_older_composite():
    history = _history_for_category(direction_correct=True, n=25, category="technicals")
    cutoff = history[-5]["timestamp"]            # only 5 calls in the current composite
    actions = accuracy.plan_calibration(
        "QQQ", history, {"technicals": 1.0}, [], [],
        _cal_cfg(confidence_min_graded=20, composite_since=cutoff), date(2026, 7, 7))
    assert not any(a["kind"] == "confidence_map" for a in actions)


# --- the autopilot's gate: a band's lower confidence bound -------------------

THIN_BAND = [{"lo": 0, "hi": 20, "observed_accuracy_pct": 56.1,
              "count": 900, "independent_count": 41}]


def test_wilson_lower_bound_known_values():
    assert accuracy.wilson_lower_bound(56.1, 41) == pytest.approx(43.4, abs=0.1)
    assert accuracy.wilson_lower_bound(56.1, 41, z=0) == pytest.approx(56.1)  # z=0: the estimate
    assert accuracy.wilson_lower_bound(56.1, 4000) == pytest.approx(54.8, abs=0.1)  # tightens with n
    assert accuracy.wilson_lower_bound(100.0, 3) > 0                     # sane at the edges
    assert accuracy.wilson_lower_bound(50.0, 0) == 0.0


def test_gate_confidence_uses_the_lower_bound_not_the_estimate():
    # the real case: raw 6% displayed as 56% cleared a 55% gate
    assert accuracy.calibrated_confidence(6.0, THIN_BAND, 10) == pytest.approx(56.1)
    gate = accuracy.gate_confidence(6.0, THIN_BAND, 10)
    assert gate < 55 and gate == pytest.approx(43.4, abs=0.1)
    assert accuracy.gate_confidence(6.0, THIN_BAND, 10, z=0) == pytest.approx(56.1)


def test_gate_confidence_falls_back_to_raw_like_the_display_does():
    assert accuracy.gate_confidence(62.0, None) == 62.0                  # no map yet
    assert accuracy.gate_confidence(25.0, THIN_BAND, 10) == 25.0         # outside every band
    assert accuracy.gate_confidence(6.0, THIN_BAND, 50) == 6.0           # band too thin to trust


# --- only grade against the price near the horizon ---------------------------------

def test_a_late_session_call_is_not_graded_on_the_next_mornings_price():
    # 15:45 ET call; the worker stops at 16:00, so the next price is 09:30 tomorrow
    evening = datetime(2026, 9, 24, 19, 45, tzinfo=timezone.utc)
    morning = datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc)
    graded = accuracy.evaluate_signal_accuracy(
        [{"timestamp": evening, "direction": "bullish", "spot_price": 100.0},
         {"timestamp": morning, "direction": "bullish", "spot_price": 103.0}], 30)
    assert graded[0]["evaluated"] is False and graded[0]["future_price"] is None


def test_grading_price_must_land_within_the_lag_limit():
    lag = accuracy.GRADE_MAX_LAG_MINUTES
    on_time = accuracy.evaluate_signal_accuracy(
        [_snap(0, "bullish", 100.0), _snap(30 + lag, "bullish", 101.0)], 30)
    assert on_time[0]["evaluated"] is True and on_time[0]["hit"] is True
    too_late = accuracy.evaluate_signal_accuracy(
        [_snap(0, "bullish", 100.0), _snap(30 + lag + 1, "bullish", 101.0)], 30)
    assert too_late[0]["evaluated"] is False


def test_a_worker_outage_leaves_calls_ungraded_not_misgraded():
    # a 2-hour hole in the data mid-session: the price 30 min later is unknown
    graded = accuracy.evaluate_signal_accuracy(
        [_snap(0, "bullish", 100.0), _snap(120, "bearish", 95.0)], 30)
    assert graded[0]["evaluated"] is False


def test_an_infinite_lag_restores_the_old_behaviour():
    import math
    graded = accuracy.evaluate_signal_accuracy(
        [_snap(0, "bullish", 100.0), _snap(24 * 60, "bullish", 103.0)], 30,
        max_lag_minutes=math.inf)
    assert graded[0]["evaluated"] is True and graded[0]["future_price"] == 103.0
