"""Dealer gamma (GEX) regime math - naive front-expiry approximation."""
import math

from signals import indicators


def test_gamma_score_positive_when_calls_dominate():
    # more call gamma*OI than put -> long-gamma / rangebound -> positive
    score = indicators.gamma_exposure_score(
        call_gammas=[0.05, 0.04], call_oi=[1000, 1000],
        put_gammas=[0.05, 0.04], put_oi=[100, 100],
    )
    assert score is not None and score > 0


def test_gamma_score_negative_when_puts_dominate():
    score = indicators.gamma_exposure_score(
        call_gammas=[0.05], call_oi=[100],
        put_gammas=[0.05], put_oi=[1000],
    )
    assert score is not None and score < 0


def test_gamma_score_balanced_is_near_zero():
    score = indicators.gamma_exposure_score(
        call_gammas=[0.05], call_oi=[500],
        put_gammas=[0.05], put_oi=[500],
    )
    assert score == 0.0


def test_gamma_score_none_when_no_gamma():
    assert indicators.gamma_exposure_score([0.0], [0], [0.0], [0]) is None
    assert indicators.gamma_exposure_score([], [], [], []) is None


def test_gamma_score_handles_none_cells():
    # yfinance can leave gamma/OI as None on illiquid strikes
    score = indicators.gamma_exposure_score(
        call_gammas=[0.05, None], call_oi=[1000, None],
        put_gammas=[None, 0.04], put_oi=[None, 100],
    )
    assert score is not None and score > 0  # only the call strike contributes real gamma


def test_gamma_score_treats_nan_as_zero():
    # NaN is truthy, so `x or 0.0` would corrupt the sum - must be coerced to 0
    nan = float("nan")
    score = indicators.gamma_exposure_score(
        call_gammas=[0.05, nan], call_oi=[1000, nan],
        put_gammas=[0.05, nan], put_oi=[500, nan],
    )
    # calls 0.05*1000=50, puts 0.05*500=25 -> (50-25)/75
    assert math.isclose(score, 25.0 / 75.0)


def test_gamma_score_is_clipped():
    score = indicators.gamma_exposure_score([0.05], [1000], [0.0], [0])
    assert score == 1.0  # all call gamma -> +1


def test_gamma_notional_sign_and_scale():
    notional = indicators.gamma_notional(
        call_gammas=[0.05], call_oi=[1000],
        put_gammas=[0.05], put_oi=[100], spot=500.0,
    )
    # net = 0.05*1000 - 0.05*100 = 45; *500^2*100*0.01
    assert math.isclose(notional, 45.0 * 500.0 * 500.0 * 100.0 * 0.01)
    assert indicators.gamma_notional([0.05], [1000], [0.0], [0], spot=0.0) is None


def test_gamma_regime_classification():
    assert indicators.gamma_regime(0.5) == "positive"
    assert indicators.gamma_regime(-0.5) == "negative"
    assert indicators.gamma_regime(0.05) == "neutral"     # inside deadband
    assert indicators.gamma_regime(-0.10, deadband=0.15) == "neutral"
    assert indicators.gamma_regime(None) is None
