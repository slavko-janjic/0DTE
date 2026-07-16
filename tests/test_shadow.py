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
