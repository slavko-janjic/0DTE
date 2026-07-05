from signals import composite

WEIGHTS = {"technicals": 0.30, "greeks_iv": 0.25, "order_flow": 0.25, "sentiment": 0.20}


def test_compute_composite_score_weighted_average():
    subscores = {"technicals": 1.0, "greeks_iv": 1.0, "order_flow": 1.0, "sentiment": 1.0}
    assert composite.compute_composite_score(subscores, WEIGHTS) == 1.0


def test_compute_composite_score_renormalizes_when_source_missing():
    # only technicals + order_flow available, both bullish -> should stay 1.0
    # after renormalizing weights among just those two, not silently dilute
    subscores = {"technicals": 1.0, "greeks_iv": None, "order_flow": 1.0, "sentiment": None}
    assert composite.compute_composite_score(subscores, WEIGHTS) == 1.0


def test_compute_composite_score_none_when_all_missing():
    subscores = {"technicals": None, "greeks_iv": None, "order_flow": None, "sentiment": None}
    assert composite.compute_composite_score(subscores, WEIGHTS) is None


def test_direction_from_score():
    assert composite.direction_from_score(0.5) == "bullish"
    assert composite.direction_from_score(-0.5) == "bearish"
    assert composite.direction_from_score(0.001) == "neutral"


def test_build_recommendation_below_floor_says_stay_out():
    rec = composite.build_recommendation("QQQ", "bullish", confidence_pct=20, confidence_floor_pct=40)
    assert "stay out" in rec.lower()


def test_build_recommendation_above_floor_suggests_direction():
    rec = composite.build_recommendation("QQQ", "bullish", confidence_pct=90, confidence_floor_pct=40)
    assert "calls" in rec.lower()
    assert "90" in rec

    rec_bear = composite.build_recommendation("SPY", "bearish", confidence_pct=75, confidence_floor_pct=40)
    assert "puts" in rec_bear.lower()


def test_compute_signal_end_to_end():
    subscores = {"technicals": 0.8, "greeks_iv": 0.6, "order_flow": 0.5, "sentiment": 0.4}
    signal = composite.compute_signal("QQQ", subscores, WEIGHTS, confidence_floor_pct=40)
    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.confidence_pct > 40
    assert "calls" in signal.recommendation.lower()
    assert signal.subscores_used == subscores


def test_compute_signal_returns_none_when_no_data():
    subscores = {"technicals": None, "greeks_iv": None, "order_flow": None, "sentiment": None}
    assert composite.compute_signal("QQQ", subscores, WEIGHTS, confidence_floor_pct=40) is None
