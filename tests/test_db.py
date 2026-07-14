import pytest

from storage import db as storage


def make_temp_db(tmp_path) -> str:
    path = str(tmp_path / "test.db")
    storage.init_db(path)
    return path


def test_ensure_worker_settings_sets_default_once(tmp_path):
    path = make_temp_db(tmp_path)
    storage.ensure_worker_settings(path, default_poll_interval_seconds=300)
    assert storage.get_poll_interval_seconds(path) == 300

    # calling again with a different default shouldn't overwrite an existing value
    storage.ensure_worker_settings(path, default_poll_interval_seconds=999)
    assert storage.get_poll_interval_seconds(path) == 300


def test_set_poll_interval_seconds_updates_value(tmp_path):
    path = make_temp_db(tmp_path)
    storage.ensure_worker_settings(path, default_poll_interval_seconds=300)
    storage.set_poll_interval_seconds(path, 30)
    assert storage.get_poll_interval_seconds(path) == 30


def test_get_poll_interval_seconds_falls_back_when_unset(tmp_path):
    path = make_temp_db(tmp_path)
    assert storage.get_poll_interval_seconds(path) == 300


def test_insert_signal_snapshot_stores_spot_price(tmp_path):
    path = make_temp_db(tmp_path)
    storage.insert_signal_snapshot(
        path, "QQQ", "bullish", 60.0, 0.6, "Buy calls", {"technicals": 0.6}, spot_price=450.25,
    )
    history = storage.get_signal_history(path, "QQQ")
    assert len(history) == 1
    assert history[0]["spot_price"] == 450.25


def test_get_signal_history_returns_oldest_first(tmp_path):
    path = make_temp_db(tmp_path)
    for score in (0.1, 0.2, 0.3):
        storage.insert_signal_snapshot(path, "QQQ", "bullish", 10.0, score, "x", {}, spot_price=100.0)
    history = storage.get_signal_history(path, "QQQ")
    assert [row["composite_score"] for row in history] == [0.1, 0.2, 0.3]


def test_clear_closed_positions_removes_only_closed(tmp_path):
    path = make_temp_db(tmp_path)
    open_id = storage.open_position(path, "QQQ", "call", 450.0, "2026-07-06", 1, 2.0, 0.5)
    closed_id = storage.open_position(path, "QQQ", "call", 451.0, "2026-07-06", 1, 2.0, 0.5)
    storage.close_position(path, closed_id, 3.0, "manual")

    storage.clear_closed_positions(path)

    assert storage.get_closed_positions(path) == []
    remaining_open = storage.get_open_positions(path)
    assert len(remaining_open) == 1
    assert remaining_open[0]["id"] == open_id


def test_backup_db_creates_dated_snapshot(tmp_path):
    path = make_temp_db(tmp_path)
    storage.insert_signal_snapshot(path, "QQQ", "bullish", 60.0, 0.6, "x", {}, spot_price=100.0)

    backup_dir = tmp_path / "backups"
    target = storage.backup_db(path, backup_dir)

    assert target is not None and target.exists()
    # backup is valid SQLite containing the same data
    assert storage.get_signal_history(target, "QQQ")[0]["spot_price"] == 100.0


def test_backup_db_skips_when_todays_backup_exists(tmp_path):
    path = make_temp_db(tmp_path)
    backup_dir = tmp_path / "backups"
    first = storage.backup_db(path, backup_dir)
    second = storage.backup_db(path, backup_dir)
    assert first is not None
    assert second is None


def test_backup_db_prunes_to_keep_newest(tmp_path):
    path = make_temp_db(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # simulate two weeks of older backups
    for day in range(1, 16):
        (backup_dir / f"0dte_2026-06-{day:02d}.db").touch()

    storage.backup_db(path, backup_dir, keep=14)

    remaining = sorted(p.name for p in backup_dir.glob("0dte_*.db"))
    assert len(remaining) == 14
    assert "0dte_2026-06-01.db" not in remaining  # oldest pruned
    assert remaining[-1].startswith("0dte_2026-07")  # today's backup kept


def test_weight_overrides_roundtrip(tmp_path):
    path = make_temp_db(tmp_path)
    assert storage.get_weight_overrides(path, "QQQ") is None

    storage.set_weight_overrides(path, "QQQ", {"technicals": 0.5, "sentiment": 0.5})
    override = storage.get_weight_overrides(path, "QQQ")
    assert override is not None
    weights, updated_at = override
    assert weights == {"technicals": 0.5, "sentiment": 0.5}
    assert updated_at  # timestamp recorded

    # setting again replaces, not duplicates
    storage.set_weight_overrides(path, "QQQ", {"technicals": 1.0})
    assert storage.get_weight_overrides(path, "QQQ")[0] == {"technicals": 1.0}

    storage.clear_weight_overrides(path, "QQQ")
    assert storage.get_weight_overrides(path, "QQQ") is None


def test_weight_overrides_are_per_ticker(tmp_path):
    path = make_temp_db(tmp_path)
    storage.set_weight_overrides(path, "QQQ", {"technicals": 0.9})
    storage.set_weight_overrides(path, "SPY", {"sentiment": 0.9})

    # each ticker keeps its own override, independently
    assert storage.get_weight_overrides(path, "QQQ")[0] == {"technicals": 0.9}
    assert storage.get_weight_overrides(path, "SPY")[0] == {"sentiment": 0.9}
    assert storage.get_weight_overrides(path, "NVDA") is None

    # clearing one leaves the other intact
    storage.clear_weight_overrides(path, "QQQ")
    assert storage.get_weight_overrides(path, "QQQ") is None
    assert storage.get_weight_overrides(path, "SPY")[0] == {"sentiment": 0.9}


def test_effective_weights_prefers_override_then_config(tmp_path):
    path = make_temp_db(tmp_path)
    config = {"weights": {"technicals": 0.7, "sentiment": 0.3}}

    assert storage.effective_weights(path, config, "QQQ") == config["weights"]

    storage.set_weight_overrides(path, "QQQ", {"technicals": 0.2, "sentiment": 0.8})
    assert storage.effective_weights(path, config, "QQQ") == {"technicals": 0.2, "sentiment": 0.8}
    # a different ticker without an override still gets config defaults
    assert storage.effective_weights(path, config, "SPY") == config["weights"]

    storage.clear_weight_overrides(path, "QQQ")
    assert storage.effective_weights(path, config, "QQQ") == config["weights"]


def test_set_position_exit_targets_roundtrip(tmp_path):
    path = make_temp_db(tmp_path)
    pid = storage.open_position(path, "QQQ", "call", 450.0, "2026-07-06", 1, 2.0, 0.5)

    # default: no per-trade targets
    row = storage.get_open_positions(path)[0]
    assert row["profit_target_pct"] is None
    assert row["stop_loss_pct"] is None

    storage.set_position_exit_targets(path, pid, profit_target_pct=20, stop_loss_pct=-10)
    row = storage.get_open_positions(path)[0]
    assert row["profit_target_pct"] == 20
    assert row["stop_loss_pct"] == -10

    # clearing back to None works
    storage.set_position_exit_targets(path, pid, profit_target_pct=None, stop_loss_pct=None)
    row = storage.get_open_positions(path)[0]
    assert row["profit_target_pct"] is None
    assert row["stop_loss_pct"] is None


def test_autopilot_state_roundtrip(tmp_path):
    path = make_temp_db(tmp_path)
    storage.ensure_autopilot(path)
    assert storage.get_autopilot_state(path) == ("off", None)
    assert storage.get_autopilot_enabled(path) is False  # off by default

    storage.set_autopilot_state(path, "continuous")
    assert storage.get_autopilot_state(path) == ("continuous", None)
    assert storage.get_autopilot_enabled(path) is True

    storage.set_autopilot_state(path, "off")
    assert storage.get_autopilot_enabled(path) is False

    # ensure never overwrites an explicit setting
    storage.set_autopilot_state(path, "continuous")
    storage.ensure_autopilot(path)
    assert storage.get_autopilot_enabled(path) is True

    with pytest.raises(ValueError):
        storage.set_autopilot_state(path, "bogus")


def test_autopilot_day_mode_active_only_on_armed_date(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    path = make_temp_db(tmp_path)
    storage.ensure_autopilot(path)

    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    storage.set_autopilot_state(path, "day", armed_date=today)
    assert storage.get_autopilot_enabled(path) is True

    storage.set_autopilot_state(path, "day", armed_date="2026-01-02")  # past date
    assert storage.get_autopilot_enabled(path) is False


def test_autopilot_migration_backfills_enabled_to_continuous(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy_ap.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE autopilot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO autopilot_settings VALUES (1, 1, '2026-07-01T00:00:00')")
    storage.init_db(path)  # migrates: adds mode/armed_date, backfills continuous
    assert storage.get_autopilot_state(path) == ("continuous", None)


def test_open_position_opened_by_tagging(tmp_path):
    path = make_temp_db(tmp_path)
    manual_id = storage.open_position(path, "QQQ", "call", 450.0, "2026-07-07", 1, 2.0, 0.5)
    auto_id = storage.open_position(path, "SPY", "put", 620.0, "2026-07-07", 1, 1.5, -0.6,
                                    opened_by="auto")
    rows = {r["id"]: r["opened_by"] for r in storage.get_open_positions(path)}
    assert rows[manual_id] == "manual"  # default
    assert rows[auto_id] == "auto"


def test_migrate_adds_opened_by_to_pre_existing_positions(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy_by.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
                option_type TEXT NOT NULL, strike REAL NOT NULL, expiration TEXT NOT NULL,
                contracts INTEGER NOT NULL, entry_price REAL NOT NULL, cost_basis REAL NOT NULL,
                entry_time TEXT NOT NULL, entry_composite_score REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', exit_price REAL, exit_time TEXT,
                exit_reason TEXT, pnl REAL, suggested_exit_reason TEXT, current_price REAL
            )
        """)
        conn.execute("""INSERT INTO positions (ticker, option_type, strike, expiration, contracts,
                        entry_price, cost_basis, entry_time, entry_composite_score)
                        VALUES ('QQQ','call',450,'2026-07-07',1,2.0,200,'2026-07-07T14:00:00+00:00',0.5)""")
    storage.init_db(path)  # migrates: adds exit targets + opened_by
    row = storage.get_open_positions(path)[0]
    assert row["opened_by"] == "manual"  # pre-existing rows default to manual


def test_migrate_adds_exit_target_columns_to_pre_existing_positions(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy_pos.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
                option_type TEXT NOT NULL, strike REAL NOT NULL, expiration TEXT NOT NULL,
                contracts INTEGER NOT NULL, entry_price REAL NOT NULL, cost_basis REAL NOT NULL,
                entry_time TEXT NOT NULL, entry_composite_score REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', exit_price REAL, exit_time TEXT,
                exit_reason TEXT, pnl REAL, suggested_exit_reason TEXT, current_price REAL
            )
        """)
    storage.init_db(path)  # should add profit_target_pct / stop_loss_pct without raising
    pid = storage.open_position(path, "QQQ", "call", 450.0, "2026-07-06", 1, 2.0, 0.5)
    storage.set_position_exit_targets(path, pid, 30, -20)
    row = storage.get_open_positions(path)[0]
    assert row["profit_target_pct"] == 30
    assert row["stop_loss_pct"] == -20


def test_migrate_adds_spot_price_column_to_pre_existing_db(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE signal_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL, timestamp TEXT NOT NULL, direction TEXT NOT NULL,
                confidence REAL NOT NULL, composite_score REAL NOT NULL,
                recommendation TEXT NOT NULL, subscores_json TEXT NOT NULL
            )
        """)
    storage.init_db(path)  # should migrate without raising
    storage.insert_signal_snapshot(path, "QQQ", "bullish", 10.0, 0.1, "x", {}, spot_price=99.0)
    assert storage.get_signal_history(path, "QQQ")[0]["spot_price"] == 99.0


# --- automatic self-calibration storage -------------------------------------

def test_calibration_enabled_default_and_toggle(tmp_path):
    path = make_temp_db(tmp_path)
    storage.ensure_calibration(path)
    assert storage.get_calibration_enabled(path) is True  # on by default
    storage.set_calibration_enabled(path, False)
    assert storage.get_calibration_enabled(path) is False


def test_last_calibration_date_roundtrip(tmp_path):
    path = make_temp_db(tmp_path)
    storage.ensure_calibration(path)
    assert storage.get_last_calibration_date(path) is None
    storage.set_last_calibration_date(path, "2026-07-10")
    assert storage.get_last_calibration_date(path) == "2026-07-10"


def test_inversions_add_get_remove(tmp_path):
    path = make_temp_db(tmp_path)
    storage.add_inversion(path, "QQQ", "sentiment")
    storage.add_inversion(path, "QQQ", "sentiment")  # idempotent
    storage.add_inversion(path, "QQQ", "order_flow")
    assert storage.get_inversions(path, "QQQ") == ["order_flow", "sentiment"]
    assert storage.get_inversions(path, "SPY") == []
    storage.remove_inversion(path, "QQQ", "sentiment")
    assert storage.get_inversions(path, "QQQ") == ["order_flow"]


def test_effective_inversions_unions_config_and_db(tmp_path):
    path = make_temp_db(tmp_path)
    storage.add_inversion(path, "QQQ", "sentiment")
    config = {"invert_categories": ["trump_news"]}
    assert storage.effective_inversions(path, config, "QQQ") == ["sentiment", "trump_news"]
    assert storage.effective_inversions(path, config, "SPY") == ["trump_news"]


def test_confidence_bands_roundtrip_and_clear(tmp_path):
    path = make_temp_db(tmp_path)
    bands = [{"lo": 60, "hi": 101, "count": 20, "observed_accuracy_pct": 70.0}]
    assert storage.get_confidence_bands(path, "QQQ") is None
    storage.set_confidence_bands(path, "QQQ", bands)
    assert storage.get_confidence_bands(path, "QQQ") == bands
    # overwrite
    storage.set_confidence_bands(path, "QQQ", [])
    assert storage.get_confidence_bands(path, "QQQ") == []
    storage.clear_confidence_bands(path, "QQQ")
    assert storage.get_confidence_bands(path, "QQQ") is None


def test_calibration_events_insert_and_list(tmp_path):
    path = make_temp_db(tmp_path)
    storage.log_calibration_event(path, "QQQ", "inversion_added", {"category": "sentiment"})
    storage.log_calibration_event(path, "SPY", "weights_nudged", {"old": {}, "new": {}})
    events = storage.get_calibration_events(path, limit=10)
    assert len(events) == 2
    assert events[0]["kind"] == "weights_nudged"  # most recent first


def test_calibrated_confidence_column_stored(tmp_path):
    path = make_temp_db(tmp_path)
    storage.insert_signal_snapshot(
        path, "QQQ", "bullish", 60.0, 0.6, "rec", {"technicals": 0.6},
        spot_price=100.0, calibrated_confidence=72.0,
    )
    row = storage.get_latest_signal(path, "QQQ")
    assert row["confidence"] == 60.0
    assert row["calibrated_confidence"] == 72.0


def test_calibrated_confidence_column_defaults_null(tmp_path):
    path = make_temp_db(tmp_path)
    storage.insert_signal_snapshot(
        path, "QQQ", "bullish", 60.0, 0.6, "rec", {"technicals": 0.6}, spot_price=100.0,
    )
    row = storage.get_latest_signal(path, "QQQ")
    assert row["calibrated_confidence"] is None


# --- pre-market day setups --------------------------------------------------

def test_day_setup_roundtrip_and_upsert(tmp_path):
    path = make_temp_db(tmp_path)
    assert storage.get_day_setup(path, "SPY", "2026-07-15") is None
    setup = {"ticker": "SPY", "gap_pct": 0.4, "prior_close": 503.0, "catalysts": []}
    storage.set_day_setup(path, "SPY", "2026-07-15", setup)
    assert storage.get_day_setup(path, "SPY", "2026-07-15") == setup
    # upsert overwrites same (ticker, date)
    updated = {"ticker": "SPY", "gap_pct": -0.2, "prior_close": 501.0, "catalysts": []}
    storage.set_day_setup(path, "SPY", "2026-07-15", updated)
    assert storage.get_day_setup(path, "SPY", "2026-07-15") == updated
    # a different date is independent
    assert storage.get_day_setup(path, "SPY", "2026-07-16") is None
