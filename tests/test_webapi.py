"""The web UI's endpoint helpers: the read payloads (pure, SQLite only) and the
write actions (honest fills, race-safe closes, autopilot arming).

These mirror what tests/ already does for the domain layer - a temp database,
no network - so the same numbers the Streamlit dashboard showed are asserted on
the JSON side too.
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from paper_trading import engine
from storage import db as storage
from webapi import live, payloads

MARKET_TZ = ZoneInfo("America/New_York")


def make_config(tmp_path) -> dict:
    return {
        "tickers": ["QQQ", "SPY"],
        "database": {"path": str(tmp_path / "test.db")},
        "account": {"starting_balance": 10000, "risk_per_trade_pct": 5},
        "poll_interval_minutes": 5,
        "accuracy_horizon_minutes": 30,
        "market_hours": {"open": "09:30", "close": "16:00", "timezone": "America/New_York"},
        "market_holidays": [],
        "market_half_days": [],
        "market_catalysts": [],
        "weights": {"technicals": 0.34, "order_flow": 0.33, "volatility_regime": 0.33},
        "autopilot": {"tickers": ["QQQ"], "min_confidence_pct": 55, "profit_target_pct": 50,
                      "stop_loss_pct": -35, "max_entries_per_session": 1,
                      "max_trades_per_day": 4, "daily_loss_limit_pct": 10,
                      "decision_start_minutes": 30, "decision_end_minutes": 90},
        "shadow_strategies": [{"name": "baseline"}],
    }


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "test.db")
    storage.init_db(path)
    storage.ensure_account(path, 10000)
    storage.ensure_worker_settings(path, 300)
    storage.ensure_autopilot(path)
    storage.ensure_calibration(path)      # same bootstrap api.py does on startup
    return path


@pytest.fixture()
def config(tmp_path):
    return make_config(tmp_path)


def add_signal(db_path, ticker="QQQ", *, minutes_ago=1, direction="bullish", confidence=72.0,
               calibrated=None, score=0.4, spot=721.0, streak=3, gamma="negative"):
    timestamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with storage.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO signal_snapshots (ticker, timestamp, direction, confidence, "
            "composite_score, recommendation, subscores_json, spot_price, "
            "calibrated_confidence, gamma_regime, gamma_score, direction_streak) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, timestamp, direction, confidence, score, "Consider buying calls",
             json.dumps({"technicals": 0.5, "order_flow": -0.2, "volatility_regime": 0.1}),
             spot, calibrated, gamma, -1.0, streak))
    return timestamp


# --- header payloads ------------------------------------------------------

def test_worker_health_reports_down_after_missed_cycles(db):
    storage.record_heartbeat(db, pid=99, note="alive")
    assert payloads.worker_health(db)["status"] == "up"

    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with storage.connect(db) as conn:
        conn.execute("UPDATE worker_heartbeat SET updated_at = ?", (stale,))
    health = payloads.worker_health(db)
    assert health["status"] == "down"
    assert "DOWN" in health["message"]


def test_worker_health_without_a_heartbeat_is_never_run(db):
    assert payloads.worker_health(db)["status"] == "never"


def test_wallet_totals_include_open_exposure(db, config):
    engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 2, 0.4)
    wallet = payloads.wallet(db, config)
    assert wallet["open_exposure"] == pytest.approx(280.0)
    assert wallet["balance"] == pytest.approx(10000 - 280.0)
    assert wallet["open_count"] == 1


def test_sentiment_strip_prefers_calibrated_confidence(db, config):
    add_signal(db, "QQQ", confidence=48.0, calibrated=72.0)
    strip = {row["ticker"]: row for row in payloads.sentiment_strip(db, config)}
    assert strip["QQQ"]["confidence"] == 72.0
    assert strip["QQQ"]["tone"] == "up"
    assert strip["SPY"]["confidence"] is None  # no signal yet


# --- signal payload -------------------------------------------------------

def test_signal_payload_surfaces_calibration_gamma_and_streak(db, config):
    add_signal(db, "QQQ", confidence=48.0, calibrated=72.0, streak=30)
    payload = payloads.signal_payload(db, config, "QQQ")

    assert payload["available"] is True
    assert payload["confidence"] == 72.0
    assert payload["raw_confidence"] == 48.0
    assert payload["gamma"]["regime"] == "negative"
    assert "Short gamma" in payload["gamma"]["label"]
    # streak is counted in polls; the UI shows minutes at the poll interval
    assert payload["streak"]["minutes"] == 30 * 5
    assert payload["suggested_type"] == "call"
    assert payload["suggested_strike"] == 721
    assert payload["stale"] is False

    meters = {score["key"]: score for score in payload["subscores"]}
    assert meters["technicals"]["meter_pct"] == pytest.approx(75.0)
    assert meters["order_flow"]["tone"] == "lo"


def test_signal_payload_without_a_signal_is_empty_not_an_error(db, config):
    payload = payloads.signal_payload(db, config, "QQQ")
    assert payload["available"] is False
    assert "worker" in payload["message"]


def test_signal_payload_marks_a_stale_signal(db, config):
    add_signal(db, "QQQ", minutes_ago=180)
    assert payloads.signal_payload(db, config, "QQQ")["stale"] is True


def test_day_setup_is_included_when_the_premarket_pass_has_run(db, config):
    today = datetime.now(MARKET_TZ).date().isoformat()
    storage.set_day_setup(db, "QQQ", today, {
        "gap_pct": 0.34, "prior_close": 721.5, "overnight_high": 723.6,
        "overnight_low": 718.9, "catalysts": [{"label": "CPI", "time": "08:30"}],
    })
    setup = payloads.signal_payload(db, config, "QQQ")["day_setup"]
    assert setup["gap_pct"] == 0.34
    assert setup["catalysts"][0]["label"] == "CPI"


# --- history / chart ------------------------------------------------------

def test_signal_history_returns_points_and_accuracy(db, config):
    for minutes in range(120, 0, -10):
        add_signal(db, "QQQ", minutes_ago=minutes, spot=700 + minutes / 10)
    history = payloads.signal_history(db, config, "QQQ", "all")
    assert len(history["points"]) == 12
    assert history["points"][0]["price"] is not None
    assert history["horizon_minutes"] == 30
    # 30-minute grading horizon: the oldest snapshots are gradeable by now
    assert history["graded_count"] > 0


def test_signal_history_range_filters_the_chart_only(db, config):
    add_signal(db, "QQQ", minutes_ago=60 * 30)   # ~1.3 days ago
    add_signal(db, "QQQ", minutes_ago=5)
    assert len(payloads.signal_history(db, config, "QQQ", "all")["points"]) == 2
    assert len(payloads.signal_history(db, config, "QQQ", "4h")["points"]) == 1


def test_signal_history_is_empty_not_an_error_without_data(db, config):
    history = payloads.signal_history(db, config, "QQQ", "session")
    assert history["points"] == []
    assert "message" in history


# --- positions / history / calendar --------------------------------------

def test_position_rows_value_at_the_live_price_when_one_is_supplied(db, config):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 3, 0.4)
    rows = payloads.position_rows(db, config, {position_id: {"price": 1.75, "spread_pct": 4.2}})
    assert rows[0]["pnl"] == pytest.approx((1.75 - 1.40) * 3 * 100)
    assert rows[0]["pnl_pct"] == pytest.approx(25.0)
    assert rows[0]["spread_pct"] == 4.2
    assert rows[0]["opened_by"] == "manual"


def test_position_rows_fall_back_to_the_workers_last_price(db, config):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 3, 0.4)
    storage.update_position_price(db, position_id, 1.20)
    rows = payloads.position_rows(db, config, {})
    assert rows[0]["current_price"] == 1.20
    assert rows[0]["pnl"] < 0


def test_trade_history_labels_a_manual_exit_as_you_closed(db, config):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4)
    engine.close(db, position_id, 1.80, "manual")
    trade = payloads.trade_history(db, config)["trades"][0]
    assert trade["exit_reason"] == "you closed"
    assert trade["pnl"] == pytest.approx(40.0)


def test_calendar_buckets_realized_pnl_by_exit_day(db, config):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4)
    engine.close(db, position_id, 1.80, "profit_target")
    today = datetime.now(MARKET_TZ)
    calendar = payloads.calendar_payload(db, config, today.year, today.month)
    cells = [cell for week in calendar["weeks"] for cell in week if cell["today"]]
    assert cells and cells[0]["pnl"] == pytest.approx(40.0)
    assert calendar["month_total"] == pytest.approx(40.0)


# --- lab / cost / autopilot ----------------------------------------------

def test_lab_payload_lists_configured_strategies_even_without_trades(db, config):
    rows = payloads.lab_payload(db, config)["rows"]
    assert [row["name"] for row in rows] == ["baseline"]
    assert rows[0]["trades"] == 0
    assert rows[0]["verdict"] == "no trades yet"


def test_cost_payload_reports_the_intraday_curve(db, config):
    for bucket in range(4):
        for sample in range(6):
            with storage.connect(db) as conn:
                conn.execute(
                    "INSERT INTO quote_snapshots (ticker, timestamp, option_type, strike, spot, "
                    "bid, ask, mid, spread_pct, minutes_since_open) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("QQQ", datetime.now(timezone.utc).isoformat(), "call", 721, 721.0,
                     1.0, 1.1, 1.05, 9.0 - bucket, bucket * 30 + sample))
    cost = payloads.cost_payload(db, config, "QQQ")
    assert len(cost["curve"]) == 4
    assert cost["at_open"] == pytest.approx(9.0)
    assert cost["midday"] == pytest.approx(6.0)  # cheapest bucket
    assert cost["rows"][0]["samples"] == 24


def test_autopilot_payload_reports_mode_and_record(db, config):
    storage.set_autopilot_state(db, "continuous")
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4,
                             opened_by="auto")
    engine.close(db, position_id, 2.10, "profit_target")
    payload = payloads.autopilot_payload(db, config)
    assert payload["mode"] == "continuous"
    assert payload["record"]["trades"] == 1
    assert payload["record"]["win_rate_pct"] == 100.0
    assert any(row["label"] == "Confidence gate" for row in payload["config"])


def test_autopilot_intents_are_empty_while_the_market_is_closed(db, config, monkeypatch):
    storage.set_autopilot_state(db, "continuous")
    monkeypatch.setattr(payloads, "is_market_open", lambda *args, **kwargs: False)
    assert payloads.autopilot_payload(db, config)["intents"] == []


# --- writes ---------------------------------------------------------------

class FakeChain:
    """Just enough of an OptionChainSnapshot for the buy path."""
    def __init__(self, bid=1.90, ask=2.10):
        self.spot = 721.35
        self.expiration = "2099-01-02"
        self.contract = {"strike": 721.0, "bid": bid, "ask": ask, "lastPrice": 2.0}
        self.calls = [self.contract]
        self.puts = [self.contract]


@pytest.fixture()
def fake_market(monkeypatch):
    chain = FakeChain()
    monkeypatch.setattr(live.market_data, "get_option_chain", lambda ticker: chain)
    monkeypatch.setattr(live.market_data, "find_atm_contract", lambda frame, spot: frame[0])
    monkeypatch.setattr(live.market_data, "find_contract_price",
                        lambda chain, option_type, strike, expiration=None: 1.90)
    monkeypatch.setattr(live.market_data, "find_contract_row",
                        lambda chain, option_type, strike, expiration=None: chain.contract if chain else None)
    return chain


def test_place_trade_fills_at_the_ask_and_applies_targets(db, config, fake_market):
    result = live.place_trade(db, config, "QQQ", "call", 500.0,
                              profit_target_pct=50, stop_loss_pct=-35)
    assert result["ok"] is True

    position = storage.get_open_positions(db)[0]
    assert position["entry_price"] == 2.10          # ask side, not mid
    assert position["contracts"] == 2               # floor(500 / 210)
    assert position["profit_target_pct"] == 50
    assert position["stop_loss_pct"] == -35
    assert storage.get_balance(db) == pytest.approx(10000 - 2.10 * 2 * 100)


def test_place_trade_rejects_an_amount_below_one_contract(db, config, fake_market):
    result = live.place_trade(db, config, "QQQ", "call", 50.0)
    assert result["ok"] is False
    assert "one contract" in result["message"]
    assert storage.get_open_positions(db) == []


def test_place_trade_without_a_quote_degrades_instead_of_raising(db, config, monkeypatch):
    monkeypatch.setattr(live.market_data, "get_option_chain",
                        lambda ticker: (_ for _ in ()).throw(RuntimeError("network down")))
    result = live.place_trade(db, config, "QQQ", "call", 500.0)
    assert result["ok"] is False
    assert "quote" in result["message"]


def test_place_trade_rejects_an_untracked_ticker(db, config, fake_market):
    assert live.place_trade(db, config, "GME", "call", 500.0)["ok"] is False


def test_close_position_credits_the_balance_once(db, config, fake_market):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 2, 0.4)
    balance_after_buy = storage.get_balance(db)

    result = live.close_position(db, config, position_id)
    assert result["ok"] is True
    assert storage.get_balance(db) == pytest.approx(balance_after_buy + 1.90 * 2 * 100)

    # a second close (worker racing the UI) must not credit again
    again = live.close_position(db, config, position_id)
    assert again["ok"] is False
    assert storage.get_balance(db) == pytest.approx(balance_after_buy + 1.90 * 2 * 100)


def test_refresh_open_positions_auto_closes_on_the_profit_target(db, config, fake_market):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.00, 1, 0.4)
    storage.set_position_exit_targets(db, position_id, 50, -35)

    quotes, closed = live.refresh_open_positions(db, config)   # live price 1.90 = +90%
    assert quotes == {}
    assert closed[0]["reason"] == "profit target"
    assert storage.get_open_positions(db) == []


def test_refresh_open_positions_keeps_a_position_short_of_its_target(db, config, fake_market):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.80, 1, 0.4)
    storage.set_position_exit_targets(db, position_id, 50, -35)
    quotes, closed = live.refresh_open_positions(db, config)   # +5.6%
    assert closed == []
    assert quotes[position_id]["price"] == 1.90


def test_set_targets_stores_the_stop_as_a_negative(db, config):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4)
    live.set_targets(db, position_id, 30, 20)
    position = storage.get_open_positions(db)[0]
    assert position["profit_target_pct"] == 30
    assert position["stop_loss_pct"] == -20

    live.set_targets(db, position_id, None, None)
    position = storage.get_open_positions(db)[0]
    assert position["profit_target_pct"] is None
    assert position["stop_loss_pct"] is None


def test_set_autopilot_mode_arms_a_day_session(db, config):
    result = live.set_autopilot_mode(db, config, "day")
    assert result["ok"] is True
    mode, armed = storage.get_autopilot_state(db)
    assert mode == "day"
    assert armed == result["armed_date"]


def test_set_autopilot_mode_rejects_an_unknown_mode(db, config):
    assert live.set_autopilot_mode(db, config, "turbo")["ok"] is False


def test_clear_history_keeps_open_positions_and_balance(db, config):
    closed_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4)
    engine.close(db, closed_id, 1.80, "manual")
    engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4)
    balance = storage.get_balance(db)

    live.clear_history(db)
    assert storage.get_closed_positions(db) == []
    assert len(storage.get_open_positions(db)) == 1
    assert storage.get_balance(db) == balance


# --- tuning: calibration, per-signal accuracy, weights --------------------

def seed_history(db_path, ticker="QQQ", count=40, spacing=10):
    """A run of snapshots old enough that the 30-minute horizon can grade them."""
    for index in range(count, 0, -1):
        add_signal(db_path, ticker, minutes_ago=index * spacing,
                   direction="bullish" if index % 2 else "bearish",
                   score=0.4 if index % 2 else -0.4,
                   spot=700 + index * 0.5, confidence=40 + (index % 5) * 10)


def test_calibration_payload_reports_state_and_grading(db, config):
    seed_history(db)
    payloads._ANALYSIS_CACHE.clear()
    payload = payloads.calibration_payload(db, config, "QQQ")

    assert payload["enabled"] is True           # ensure_calibration default
    assert payload["last_run"] is None
    assert payload["learning_rate_pct"] == pytest.approx(25.0)
    assert payload["graded_count"] > 0
    assert {row["key"] for row in payload["categories"]} == {
        "technicals", "order_flow", "volatility_regime"}
    assert payload["confidence_bands"]          # graded calls land in confidence bands
    assert set(payload["context"]) == {"time_of_day", "volatility", "streak"}


def test_calibration_payload_lists_events_and_inversions(db, config):
    storage.add_inversion(db, "QQQ", "order_flow")
    storage.log_calibration_event(db, "QQQ", "inversion_added",
                                  {"category": "order_flow", "accuracy_pct": 31.0})
    payload = payloads.calibration_payload(db, config, "QQQ")
    assert payload["inversions"] == [{"key": "order_flow", "name": "Order flow"}]
    assert payload["events"][0]["action"] == "inverted"
    assert "Order flow" in payload["events"][0]["detail"]


def test_analysis_is_cached_until_a_new_snapshot_lands(db, config):
    seed_history(db, count=5)
    payloads._ANALYSIS_CACHE.clear()
    first = payloads._analysis(db, config, "QQQ")
    assert payloads._analysis(db, config, "QQQ") is first     # same object: cache hit

    add_signal(db, "QQQ", minutes_ago=0)
    assert payloads._analysis(db, config, "QQQ") is not first  # fingerprint moved


def test_apply_and_revert_weights_round_trip(db, config, monkeypatch):
    seed_history(db)
    payloads._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(payloads.accuracy, "suggest_weights",
                        lambda *args, **kwargs: {"technicals": 0.5, "order_flow": 0.3,
                                                 "volatility_regime": 0.2})
    assert live.apply_weights(db, config, "QQQ")["ok"] is True
    assert storage.effective_weights(db, config, "QQQ")["technicals"] == pytest.approx(0.5)

    assert live.revert_weights(db, config, "QQQ")["ok"] is True
    assert storage.get_weight_overrides(db, "QQQ") is None
    assert storage.effective_weights(db, config, "QQQ") == config["weights"]


def test_apply_weights_without_enough_history_is_rejected(db, config):
    payloads._ANALYSIS_CACHE.clear()
    result = live.apply_weights(db, config, "QQQ")
    assert result["ok"] is False
    assert "graded history" in result["message"]


def test_calibration_toggle_persists(db):
    live.set_calibration_enabled(db, False)
    assert storage.get_calibration_enabled(db) is False
    live.set_calibration_enabled(db, True)
    assert storage.get_calibration_enabled(db) is True


def test_revert_calibration_clears_overrides_inversions_and_bands(db, config):
    for ticker in config["tickers"]:
        storage.set_weight_overrides(db, ticker, {"technicals": 1.0})
        storage.add_inversion(db, ticker, "order_flow")
        storage.set_confidence_bands(db, ticker, [{"lo": 0, "hi": 50, "accuracy_pct": 40}])

    live.revert_calibration(db, config)

    for ticker in config["tickers"]:
        assert storage.get_weight_overrides(db, ticker) is None
        assert storage.get_inversions(db, ticker) == []
        assert storage.get_confidence_bands(db, ticker) is None
    assert any(event["kind"] == "reverted" for event in storage.get_calibration_events(db))


# --- the ARMED alert's data ------------------------------------------------

def test_overview_exposes_armed_tickers_and_open_auto_positions(db, config, monkeypatch):
    storage.set_autopilot_state(db, "continuous")
    engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4, opened_by="auto")
    monkeypatch.setattr(payloads, "is_market_open", lambda *args, **kwargs: False)

    autopilot = payloads.overview(db, config)["autopilot"]
    assert autopilot["armed"] == []                       # market closed: nothing armed
    assert autopilot["auto_open"][0]["ticker"] == "QQQ"


def test_autopilot_intents_are_the_workers_own_decision(db, config, monkeypatch):
    add_signal(db, "QQQ", confidence=80.0, direction="bullish")
    storage.set_autopilot_state(db, "continuous")
    monkeypatch.setattr(payloads, "is_market_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(payloads, "minutes_since_market_open", lambda *args, **kwargs: 45)
    monkeypatch.setattr(payloads, "minutes_to_market_close", lambda *args, **kwargs: 300)

    intents = payloads.autopilot_intents(db, config)
    assert [intent["ticker"] for intent in intents] == ["QQQ"]   # the whitelist
    assert intents[0]["lean"] == "call"
    # armed or not, the decision always carries its reason
    assert intents[0]["would_enter"] or intents[0]["blocker"]


def test_close_position_refuses_rather_than_booking_flat_without_a_price(db, config, monkeypatch):
    monkeypatch.setattr(live.market_data, "get_option_chain", lambda ticker: None)
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2099-01-02", 1.40, 1, 0.4)
    balance = storage.get_balance(db)
    result = live.close_position(db, config, position_id)
    assert result["ok"] is False
    assert storage.get_balance(db) == balance
    assert len(storage.get_open_positions(db)) == 1


def test_close_position_settles_an_expired_contract(db, config, fake_market):
    position_id = engine.buy(db, "QQQ", "call", 721.0, "2020-01-02", 1.40, 1, 0.4)
    result = live.close_position(db, config, position_id)   # no final spot -> 0.00
    assert result["ok"] is True
    closed = storage.get_closed_positions(db)[0]
    assert closed["exit_reason"] == "expired"
    assert closed["exit_price"] == 0.0     # not the live 1.90 of some other expiry


def test_refresh_settles_expired_positions_instead_of_repricing_them(db, config, fake_market):
    engine.buy(db, "QQQ", "call", 721.0, "2020-01-02", 1.40, 1, 0.4)
    quotes, _closed = live.refresh_open_positions(db, config)
    assert quotes == {}
    assert storage.get_open_positions(db) == []
    assert storage.get_closed_positions(db)[0]["exit_reason"] == "expired"


# --- accuracy window --------------------------------------------------------

def test_history_reaches_past_the_old_2000_row_cap(db, config):
    # 2,100 one-minute snapshots: the old row cap silently dropped the oldest
    with storage.connect(db) as conn:
        base = datetime.now(timezone.utc) - timedelta(minutes=2200)
        conn.executemany(
            "INSERT INTO signal_snapshots (ticker, timestamp, direction, confidence, "
            "composite_score, recommendation, subscores_json, spot_price) "
            "VALUES ('QQQ', ?, 'bullish', 50, 0.5, 'r', '{\"technicals\": 0.5}', 700)",
            [((base + timedelta(minutes=i)).isoformat(),) for i in range(2100)])
    payloads._ANALYSIS_CACHE.clear()
    snapshots, _evaluated, _categories, _bands = payloads._analysis(db, config, "QQQ")
    assert len(snapshots) == 2100


def test_accuracy_counts_only_the_current_composite(db, config):
    for minutes in range(600, 0, -10):
        add_signal(db, "QQQ", minutes_ago=minutes, spot=700 + minutes / 10)
    payloads._ANALYSIS_CACHE.clear()
    everything = payloads.signal_history(db, config, "QQQ", "all")

    cutoff = datetime.now(MARKET_TZ).date().isoformat()
    config["composite_since"] = cutoff
    payloads._ANALYSIS_CACHE.clear()
    current = payloads.signal_history(db, config, "QQQ", "all")
    assert current["composite_since"] == cutoff
    assert current["window_days"] == 30
    # the chart still shows the whole window...
    assert len(current["points"]) == len(everything["points"])
    # ...but only today's calls are graded (fewer, unless it is just past midnight)
    assert current["graded_count"] <= everything["graded_count"]
    assert all(day["date"] >= cutoff for day in current["daily"])


def test_tuning_page_never_lists_a_removed_signal(db, config):
    add_signal(db, "QQQ", minutes_ago=90)
    with storage.connect(db) as conn:
        conn.execute("UPDATE signal_snapshots SET subscores_json = ?",
                     (json.dumps({"technicals": 0.5, "trump_news": 0.9}),))
    for minutes in range(80, 0, -10):
        add_signal(db, "QQQ", minutes_ago=minutes)
    payloads._ANALYSIS_CACHE.clear()
    keys = {row["key"] for row in payloads.calibration_payload(db, config, "QQQ")["categories"]}
    assert "trump_news" not in keys
    assert "technicals" in keys


def test_long_chart_ranges_are_thinned_but_keep_the_latest_point():
    items = list(range(5000))
    thinned = payloads._thin(items, 800)
    assert len(thinned) == 800
    assert thinned[0] == 0 and thinned[-1] == 4999
    assert thinned == sorted(thinned)
    assert payloads._thin(items[:390], 800) == items[:390]    # a session is untouched


def test_autopilot_intents_gate_on_the_band_lower_bound(db, config, monkeypatch):
    # raw 6%, shown as its band's 56% hit rate - but 41 calls only support ~43%
    add_signal(db, "QQQ", confidence=6.0, calibrated=56.1, direction="bullish")
    storage.set_confidence_bands(db, "QQQ", [
        {"lo": 0, "hi": 20, "observed_accuracy_pct": 56.1, "count": 900, "independent_count": 41}])
    storage.set_autopilot_state(db, "continuous")
    monkeypatch.setattr(payloads, "is_market_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(payloads, "minutes_since_market_open", lambda *args, **kwargs: 45)
    monkeypatch.setattr(payloads, "minutes_to_market_close", lambda *args, **kwargs: 300)

    intent = payloads.autopilot_intents(db, config)[0]
    assert intent["would_enter"] is False
    assert intent["shown_confidence_pct"] == pytest.approx(56.1)
    assert intent["confidence_pct"] == pytest.approx(43.4, abs=0.1)
    assert "56% shown" in intent["blocker"] and "supports 43%" in intent["blocker"]

    config["calibration"] = {"gate_lower_bound_z": 0}          # the old rule would have armed
    assert payloads.autopilot_intents(db, config)[0]["would_enter"] is True
