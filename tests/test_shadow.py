"""Shadow strategy lab: entry rules and the per-strategy scorecard."""
import pytest

from paper_trading.shadow import should_shadow_enter, shadow_position_from_row, strategy_scorecard


BASE_ENTRY = {"min_confidence_pct": 55, "window_start_minutes": 30,
              "window_end_minutes": 90, "no_entry_last_minutes": 45,
              "max_entries_per_day": 1}


def _enter(entry_cfg=BASE_ENTRY, direction="bullish", confidence=60.0,
           since_open=60, to_close=300, gamma_regime=None, subscores=None,
           has_open=False, entries_today=0, direction_streak=1):
    return should_shadow_enter(
        entry_cfg, direction, confidence, since_open, to_close,
        gamma_regime, subscores or {}, has_open, entries_today, direction_streak,
    )


def test_shadow_enter_happy_paths():
    assert _enter(direction="bullish") == "call"
    assert _enter(direction="bearish") == "put"
    assert _enter(direction="neutral") is None


def test_shadow_enter_confidence_and_window():
    assert _enter(confidence=50.0) is None
    assert _enter(since_open=20) is None       # before window
    assert _enter(since_open=120) is None      # after window
    assert _enter(to_close=30) is None         # not enough runway
    # no window keys = whole session
    open_cfg = {"min_confidence_pct": 55}
    assert _enter(entry_cfg=open_cfg, since_open=300) == "call"


def test_shadow_enter_gamma_block():
    cfg = {**BASE_ENTRY, "gamma_block": ["positive"]}
    assert _enter(entry_cfg=cfg, gamma_regime="positive") is None
    assert _enter(entry_cfg=cfg, gamma_regime="negative") == "call"
    assert _enter(entry_cfg=cfg, gamma_regime=None) == "call"  # unknown regime doesn't block


def test_shadow_enter_confluence():
    cfg = {**BASE_ENTRY, "require_agree": "technicals"}
    assert _enter(entry_cfg=cfg, subscores={"technicals": 0.4}) == "call"        # agrees
    assert _enter(entry_cfg=cfg, subscores={"technicals": -0.4}) is None          # disagrees
    assert _enter(entry_cfg=cfg, subscores={}) is None                            # missing blocks
    bear = _enter(entry_cfg=cfg, direction="bearish", subscores={"technicals": -0.4})
    assert bear == "put"


def test_shadow_enter_dedup_and_daily_cap():
    assert _enter(has_open=True) is None
    assert _enter(entries_today=1) is None
    cfg = {**BASE_ENTRY, "max_entries_per_day": 3}
    assert _enter(entry_cfg=cfg, entries_today=2) == "call"


def test_shadow_position_from_row_carries_exit_cfg():
    row = {"id": 7, "ticker": "QQQ", "option_type": "call", "strike": 500.0,
           "expiration": "2026-07-15", "contracts": 1, "entry_price": 2.0,
           "status": "open", "current_price": 2.4, "max_price": 2.6}
    p = shadow_position_from_row(row, {"profit_target_pct": 25, "stop_loss_pct": -20})
    assert p.profit_target_pct == 25 and p.stop_loss_pct == -20
    assert p.max_price == 2.6 and p.entry_price == 2.0


def _closed(strategy, pnl, exit_time):
    return {"strategy": strategy, "pnl": pnl, "exit_time": exit_time}


def test_strategy_scorecard_math():
    rows = [
        _closed("baseline", 100.0, "2026-07-10T18:00:00+00:00"),
        _closed("baseline", -50.0, "2026-07-11T18:00:00+00:00"),
        _closed("baseline", 80.0, "2026-07-12T18:00:00+00:00"),
        _closed("runner", -30.0, "2026-07-10T18:00:00+00:00"),
    ]
    cards = strategy_scorecard(rows)
    base = cards["baseline"]
    assert base["trades"] == 3
    assert base["win_rate_pct"] == pytest.approx(2 / 3 * 100)
    assert base["total_pnl"] == pytest.approx(130.0)
    assert base["profit_factor"] == pytest.approx(180.0 / 50.0)
    assert base["max_drawdown"] == pytest.approx(50.0)  # 100 -> 50 dip
    runner = cards["runner"]
    assert runner["trades"] == 1
    assert runner["profit_factor"] == 0.0  # only a loss: gross wins 0 / gross losses 30


def test_strategy_scorecard_profit_factor_edge_cases():
    all_wins = strategy_scorecard([_closed("s", 10.0, "t")])["s"]
    assert all_wins["profit_factor"] is None  # no losses -> undefined, not inf
    all_losses = strategy_scorecard([_closed("s", -10.0, "t")])["s"]
    assert all_losses["profit_factor"] == 0.0
    assert strategy_scorecard([]) == {}


def test_shadow_enter_invert_flips_direction():
    cfg = {**BASE_ENTRY, "invert": True}
    assert _enter(entry_cfg=cfg, direction="bullish") == "put"   # fades the bullish call
    assert _enter(entry_cfg=cfg, direction="bearish") == "call"
    assert _enter(entry_cfg=cfg, direction="neutral") is None    # nothing to fade


def test_shadow_enter_invert_still_respects_other_gates():
    cfg = {**BASE_ENTRY, "invert": True}
    assert _enter(entry_cfg=cfg, confidence=40.0) is None   # confidence floor still applies
    assert _enter(entry_cfg=cfg, since_open=200) is None    # window still applies


def test_shadow_enter_min_direction_streak():
    cfg = {**BASE_ENTRY, "min_direction_streak": 10}
    assert _enter(entry_cfg=cfg, direction_streak=3) is None    # blip, not settled
    assert _enter(entry_cfg=cfg, direction_streak=10) == "call"  # boundary
    assert _enter(entry_cfg=cfg, direction_streak=25) == "call"


def test_shadow_enter_max_direction_streak():
    cfg = {**BASE_ENTRY, "max_direction_streak": 2}
    assert _enter(entry_cfg=cfg, direction_streak=1) == "call"   # fresh flip
    assert _enter(entry_cfg=cfg, direction_streak=2) == "call"
    assert _enter(entry_cfg=cfg, direction_streak=3) is None      # gone stale


def test_shadow_enter_streak_gates_unset_means_no_gate():
    assert _enter(direction_streak=1) == "call"
    assert _enter(direction_streak=999) == "call"


# --- edge tracker ------------------------------------------------------------

from paper_trading.shadow import strategy_edge


def _pnl_rows(strategy, pnls):
    return [{"strategy": strategy, "pnl": p, "exit_time": f"t{i}"} for i, p in enumerate(pnls)]


def test_strategy_edge_warming_up_below_min_trades():
    e = strategy_edge(_pnl_rows("s", [10.0, -5.0, 8.0]), min_trades=20)["s"]
    assert e["trades"] == 3
    assert e["verdict"] == "warming up (3/20)"


def test_strategy_edge_detects_no_edge_when_pnl_is_noise():
    # symmetric wins/losses -> mean ~0 -> no edge no matter how many trades
    pnls = [50.0, -50.0] * 15
    e = strategy_edge(_pnl_rows("s", pnls), min_trades=20)["s"]
    assert e["trades"] == 30
    assert abs(e["t_stat"]) < 2
    assert e["verdict"] == "no edge"


def test_strategy_edge_flags_a_real_positive_edge():
    # consistently +10 with small spread -> large t
    pnls = [10.0, 12.0, 9.0, 11.0, 10.0] * 6
    e = strategy_edge(_pnl_rows("s", pnls), min_trades=20)["s"]
    assert e["t_stat"] > 2
    assert e["verdict"] == "EDGE (+)"


def test_strategy_edge_flags_a_reliably_losing_strategy():
    pnls = [-10.0, -12.0, -9.0, -11.0, -10.0] * 6
    e = strategy_edge(_pnl_rows("s", pnls), min_trades=20)["s"]
    assert e["t_stat"] < -2
    assert e["verdict"] == "EDGE (-)"


def test_strategy_edge_trades_needed_scales_with_noise():
    tight = strategy_edge(_pnl_rows("tight", [10.0, 11.0, 9.0, 10.0] * 6))["tight"]
    noisy = strategy_edge(_pnl_rows("noisy", [100.0, -80.0, 90.0, -70.0] * 6))["noisy"]
    # a big mean buried in a big spread needs far more trades to prove
    assert tight["trades_needed"] < noisy["trades_needed"]


def test_strategy_edge_empty_and_single_trade():
    assert strategy_edge([]) == {}
    one = strategy_edge(_pnl_rows("s", [42.0]))["s"]
    assert one["trades"] == 1 and one["t_stat"] == 0.0  # no spread to test against


from paper_trading.engine import evaluate_exit
from paper_trading.shadow import shadow_exit_score

FADE_EXIT = {"profit_target_pct": 40, "stop_loss_pct": -30,
             "time_cutoff_minutes_before_close": 30, "reversal_confidence_pct": 60}


def test_shadow_exit_score_flips_only_for_inverted_strategies():
    assert shadow_exit_score({"invert": True}, 0.7) == -0.7
    assert shadow_exit_score({}, 0.7) == 0.7
    assert shadow_exit_score({"invert": True}, None) is None


def test_inverted_strategy_not_shaken_out_by_the_signal_it_fades():
    # fade_conviction buys a put when the composite is >= 60% bullish. With the
    # raw score, the reversal exit (put + composite >= +0.60) was already true
    # at entry and closed the position on the next cycle.
    entry_cfg = {"min_confidence_pct": 60, "invert": True}
    row = {"id": 1, "ticker": "SPY", "option_type": "put", "strike": 500.0,
           "expiration": "2026-09-24", "contracts": 1, "entry_price": 1.00,
           "status": "open", "current_price": 1.00, "max_price": 1.00}
    position = shadow_position_from_row(row, FADE_EXIT)

    still_bullish = shadow_exit_score(entry_cfg, 0.65)
    assert evaluate_exit(position, 1.00, still_bullish, 200, FADE_EXIT) is None
    # the faded signal flipping bearish means the inverted read turned bullish
    # -> against the put -> that IS a reversal for this strategy
    flipped = shadow_exit_score(entry_cfg, -0.65)
    assert evaluate_exit(position, 1.00, flipped, 200, FADE_EXIT) == "signal_reversal"
