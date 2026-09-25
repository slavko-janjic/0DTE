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
    from datetime import date
    path = make_temp_db(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # simulate two weeks of older backups, dated well before today
    for day in range(1, 16):
        (backup_dir / f"0dte_2020-06-{day:02d}.db").touch()

    storage.backup_db(path, backup_dir, keep=14)

    remaining = sorted(p.name for p in backup_dir.glob("0dte_*.db"))
    assert len(remaining) == 14
    assert "0dte_2020-06-01.db" not in remaining  # oldest pruned
    # today's backup is the newest and is kept (date-agnostic - the suite must
    # not break just because the calendar rolled forward)
    assert remaining[-1] == f"0dte_{date.today().isoformat()}.db"


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
    config = {"invert_categories": ["greeks_iv"]}
    assert storage.effective_inversions(path, config, "QQQ") == ["greeks_iv", "sentiment"]
    assert storage.effective_inversions(path, config, "SPY") == ["greeks_iv"]


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


# --- gamma regime columns on snapshots --------------------------------------

def test_gamma_columns_stored_and_default_null(tmp_path):
    path = make_temp_db(tmp_path)
    storage.insert_signal_snapshot(
        path, "QQQ", "bullish", 60.0, 0.6, "rec", {"technicals": 0.6},
        spot_price=100.0, gamma_score=0.42, gamma_regime="positive",
    )
    row = storage.get_latest_signal(path, "QQQ")
    assert row["gamma_score"] == 0.42
    assert row["gamma_regime"] == "positive"

    storage.insert_signal_snapshot(
        path, "SPY", "bullish", 60.0, 0.6, "rec", {"technicals": 0.6}, spot_price=100.0,
    )
    row2 = storage.get_latest_signal(path, "SPY")
    assert row2["gamma_score"] is None
    assert row2["gamma_regime"] is None


# --- max_price high-water mark ----------------------------------------------

def test_max_price_initialized_to_entry_and_bumps_up(tmp_path):
    path = make_temp_db(tmp_path)
    storage.ensure_account(path, 10000.0)
    pid = storage.open_position(path, "QQQ", "call", 500.0, "2026-07-14", 1, 2.0, 0.5)
    row = {r["id"]: r for r in storage.get_open_positions(path)}[pid]
    assert row["max_price"] == 2.0  # seeded at entry_price

    storage.update_position_price(path, pid, 2.5)      # new high
    row = {r["id"]: r for r in storage.get_open_positions(path)}[pid]
    assert row["current_price"] == 2.5 and row["max_price"] == 2.5

    storage.update_position_price(path, pid, 2.1)      # pullback: peak holds
    row = {r["id"]: r for r in storage.get_open_positions(path)}[pid]
    assert row["current_price"] == 2.1 and row["max_price"] == 2.5


# --- shadow strategy positions -----------------------------------------------

def test_shadow_position_lifecycle(tmp_path):
    path = make_temp_db(tmp_path)
    pid = storage.open_shadow_position(
        path, "baseline", "QQQ", "call", 500.0, "2026-07-15", 2.10,
        {"confidence": 62.0, "gamma_regime": "positive"},
    )
    rows = storage.get_open_shadow_positions(path, "QQQ")
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"] == "baseline"
    assert row["entry_price"] == 2.10
    assert row["max_price"] == 2.10   # seeded at entry
    import json as _json
    assert _json.loads(row["entry_reason_json"])["confidence"] == 62.0

    storage.update_shadow_price(path, pid, 2.6)
    storage.update_shadow_price(path, pid, 2.3)  # pullback keeps the peak
    row = storage.get_open_shadow_positions(path, "QQQ")[0]
    assert row["current_price"] == 2.3 and row["max_price"] == 2.6

    pnl = storage.close_shadow_position(path, pid, 2.4, "profit_target")
    assert pnl == pytest.approx((2.4 - 2.10) * 100)
    assert storage.get_open_shadow_positions(path) == []
    closed = storage.get_closed_shadow_positions(path)
    assert closed[0]["exit_reason"] == "profit_target"
    assert closed[0]["pnl_pct"] == pytest.approx((2.4 - 2.10) / 2.10 * 100.0)

    # double close is a no-op
    assert storage.close_shadow_position(path, pid, 2.4, "again") is None


def test_count_shadow_entries_today(tmp_path):
    path = make_temp_db(tmp_path)
    storage.open_shadow_position(path, "baseline", "QQQ", "call", 500.0, "2026-07-15", 2.0, {})
    storage.open_shadow_position(path, "baseline", "SPY", "call", 600.0, "2026-07-15", 2.0, {})
    storage.open_shadow_position(path, "runner", "QQQ", "call", 500.0, "2026-07-15", 2.0, {})
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    assert storage.count_shadow_entries_today(path, "baseline", "QQQ", today) == 1
    assert storage.count_shadow_entries_today(path, "baseline", "SPY", today) == 1
    assert storage.count_shadow_entries_today(path, "runner", "SPY", today) == 0
    # a date in the future counts nothing
    assert storage.count_shadow_entries_today(path, "baseline", "QQQ", "2099-01-01") == 0


# --- direction streak --------------------------------------------------------

def test_direction_streak_stored_and_next_streak_increments(tmp_path):
    path = make_temp_db(tmp_path)
    assert storage.next_direction_streak(path, "QQQ", "bullish") == 1  # no history
    storage.insert_signal_snapshot(path, "QQQ", "bullish", 60.0, 0.6, "r", {}, direction_streak=1)
    assert storage.get_latest_signal(path, "QQQ")["direction_streak"] == 1

    # same direction, fresh poll -> continues
    assert storage.next_direction_streak(path, "QQQ", "bullish") == 2
    storage.insert_signal_snapshot(path, "QQQ", "bullish", 61.0, 0.61, "r", {}, direction_streak=2)
    assert storage.next_direction_streak(path, "QQQ", "bullish") == 3
    # flip resets
    assert storage.next_direction_streak(path, "QQQ", "bearish") == 1


def test_next_direction_streak_resets_after_a_long_gap(tmp_path):
    path = make_temp_db(tmp_path)
    import sqlite3 as _sq
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    storage.insert_signal_snapshot(path, "QQQ", "bullish", 60.0, 0.6, "r", {}, direction_streak=9)
    # age the snapshot past the gap threshold (simulating the overnight break)
    stale = (_dt.now(_tz.utc) - _td(hours=17)).isoformat()
    conn = _sq.connect(path)
    conn.execute("UPDATE signal_snapshots SET timestamp = ?", (stale,))
    conn.commit(); conn.close()
    assert storage.next_direction_streak(path, "QQQ", "bullish") == 1  # not 10


def test_backfill_direction_streaks_over_existing_history(tmp_path):
    import sqlite3 as _sq
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    path = str(tmp_path / "legacy.db")
    # build a table WITHOUT direction_streak, like a pre-upgrade database
    conn = _sq.connect(path)
    conn.execute("""CREATE TABLE signal_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, timestamp TEXT NOT NULL,
        direction TEXT NOT NULL, confidence REAL NOT NULL, composite_score REAL NOT NULL,
        recommendation TEXT NOT NULL, subscores_json TEXT NOT NULL, spot_price REAL)""")
    base = _dt(2026, 7, 6, 14, 0, tzinfo=_tz.utc)
    # bullish x3, bearish x2, then a 17h gap then bullish (streak must restart)
    plan = [(0, "bullish"), (1, "bullish"), (2, "bullish"),
            (3, "bearish"), (4, "bearish"), (17 * 60 + 5, "bullish")]
    for offset, direction in plan:
        conn.execute(
            "INSERT INTO signal_snapshots (ticker,timestamp,direction,confidence,"
            "composite_score,recommendation,subscores_json) VALUES ('QQQ',?,?,50,0.5,'r','{}')",
            ((base + _td(minutes=offset)).isoformat(), direction),
        )
    conn.commit(); conn.close()

    storage.init_db(path)  # triggers the ALTER + backfill

    conn = _sq.connect(path)
    streaks = [r[0] for r in conn.execute(
        "SELECT direction_streak FROM signal_snapshots ORDER BY timestamp")]
    conn.close()
    assert streaks == [1, 2, 3, 1, 2, 1]  # runs counted; the overnight gap restarts


# --- quote snapshots (cost of trading) ---------------------------------------

def test_quote_snapshot_roundtrip_and_history(tmp_path):
    path = make_temp_db(tmp_path)
    assert storage.get_latest_quote(path, "NVDA") is None
    storage.insert_quote_snapshot(path, "NVDA", "call", 200.0, 199.5,
                                  1.90, 2.10, 2.00, 10.0, 45.0)
    storage.insert_quote_snapshot(path, "NVDA", "call", 201.0, 200.5,
                                  1.95, 2.00, 1.975, 2.5, 75.0)
    storage.insert_quote_snapshot(path, "SPY", "put", 620.0, 620.1,
                                  1.00, 1.30, 1.15, 26.0, 45.0)

    latest = storage.get_latest_quote(path, "NVDA")
    assert latest["spread_pct"] == 2.5          # most recent NVDA row
    assert latest["minutes_since_open"] == 75.0

    nvda = storage.get_quote_history(path, "NVDA")
    assert len(nvda) == 2
    assert nvda[0]["spread_pct"] == 10.0        # oldest-first
    assert [r["ticker"] for r in storage.get_quote_history(path)] == ["NVDA", "NVDA", "SPY"]


def test_quote_snapshot_tolerates_missing_quotes(tmp_path):
    path = make_temp_db(tmp_path)
    # an illiquid strike with no bid/ask still records the attempt
    storage.insert_quote_snapshot(path, "IWM", "call", 220.0, 219.0,
                                  None, None, None, None, 30.0)
    row = storage.get_latest_quote(path, "IWM")
    assert row["bid"] is None and row["spread_pct"] is None
    assert row["strike"] == 220.0


# --- VIX snapshots + baselines -----------------------------------------------

def test_vix_snapshot_computes_term_ratio(tmp_path):
    path = make_temp_db(tmp_path)
    storage.insert_vix_snapshot(path, vix=20.0, vix9d=18.0, vvix=95.0)
    import sqlite3 as _sq
    conn = _sq.connect(path); conn.row_factory = _sq.Row
    row = conn.execute("SELECT * FROM vix_snapshots").fetchone()
    conn.close()
    assert row["term_ratio"] == pytest.approx((18.0 - 20.0) / 20.0)   # -0.10 contango
    assert row["vvix"] == 95.0


def test_vix_baselines_none_until_enough_history(tmp_path):
    """No baseline -> volatility_regime stays absent. Better than scoring
    against a guessed constant, which is how it became a permanent +0.44."""
    path = make_temp_db(tmp_path)
    for _ in range(10):
        storage.insert_vix_snapshot(path, 20.0, 18.0, 95.0)
    assert storage.get_vix_baselines(path, min_samples=200) is None


def test_vix_baselines_median_is_robust_to_a_bad_print(tmp_path):
    path = make_temp_db(tmp_path)
    for _ in range(199):
        storage.insert_vix_snapshot(path, 20.0, 18.0, 95.0)      # ratio -0.10
    storage.insert_vix_snapshot(path, 20.0, 2.0, 9999.0)         # one garbage print
    b = storage.get_vix_baselines(path, min_samples=200)
    assert b is not None
    assert b["term_ratio"] == pytest.approx(-0.10)   # median shrugs it off
    assert b["vvix"] == pytest.approx(95.0)
    assert b["samples"] == 200


# --- worker heartbeat (liveness) ---------------------------------------------

def test_heartbeat_none_before_worker_runs(tmp_path):
    path = make_temp_db(tmp_path)
    assert storage.get_heartbeat(path) is None


def test_heartbeat_roundtrip_and_upsert(tmp_path):
    path = make_temp_db(tmp_path)
    storage.record_heartbeat(path, pid=1234, note="polled")
    hb = storage.get_heartbeat(path)
    assert hb["pid"] == 1234 and hb["note"] == "polled"
    assert hb["age_seconds"] < 5      # just written

    # single row - a second heartbeat overwrites, not appends
    storage.record_heartbeat(path, pid=5678, note="market closed")
    hb = storage.get_heartbeat(path)
    assert hb["pid"] == 5678 and hb["note"] == "market closed"
    import sqlite3
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM worker_heartbeat").fetchone()[0] == 1


def test_heartbeat_age_reflects_a_stale_write(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    path = make_temp_db(tmp_path)
    storage.record_heartbeat(path, pid=1, note="old")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(path) as c:
        c.execute("UPDATE worker_heartbeat SET updated_at = ? WHERE id = 1", (stale,))
    hb = storage.get_heartbeat(path)
    assert hb["age_seconds"] > 300    # ~10 min -> clearly stale


def test_effective_weights_follow_the_configs_categories(tmp_path):
    # an override saved before signals were removed/added: dead keys are
    # ignored, and a category the override never knew falls back to config
    path = make_temp_db(tmp_path)
    config = {"weights": {"technicals": 0.4, "order_flow": 0.3, "volatility_regime": 0.3}}
    storage.set_weight_overrides(path, "QQQ", {"technicals": 0.2, "order_flow": 0.1,
                                               "trump_news": 0.7})
    assert storage.effective_weights(path, config, "QQQ") == {
        "technicals": 0.2, "order_flow": 0.1, "volatility_regime": 0.3}


def test_get_final_spot_picks_the_last_print_in_the_window(tmp_path):
    path = make_temp_db(tmp_path)
    for ts, spot in [("2026-09-24T19:58:00+00:00", 501.0),
                     ("2026-09-24T19:59:30+00:00", 502.5),
                     ("2026-09-25T14:00:00+00:00", 510.0)]:   # next day - outside
        storage.insert_signal_snapshot(path, "QQQ", "bullish", 50.0, 0.5, "r", {},
                                       spot_price=spot)
        with storage.connect(path) as conn:
            conn.execute("UPDATE signal_snapshots SET timestamp = ? WHERE id = "
                         "(SELECT MAX(id) FROM signal_snapshots)", (ts,))
    assert storage.get_final_spot(path, "QQQ", "2026-09-24T13:30:00+00:00",
                                  "2026-09-24T20:05:00+00:00") == 502.5
    assert storage.get_final_spot(path, "SPY", "2026-09-24T13:30:00+00:00",
                                  "2026-09-24T20:05:00+00:00") is None


def test_get_signal_history_since_returns_the_whole_window(tmp_path):
    path = make_temp_db(tmp_path)
    for ts in ["2026-09-01T14:00:00+00:00", "2026-09-10T14:00:00+00:00",
               "2026-09-20T14:00:00+00:00"]:
        storage.insert_signal_snapshot(path, "QQQ", "bullish", 50.0, 0.5, "r", {}, spot_price=1.0)
        with storage.connect(path) as conn:
            conn.execute("UPDATE signal_snapshots SET timestamp = ? WHERE id = "
                         "(SELECT MAX(id) FROM signal_snapshots)", (ts,))
    rows = storage.get_signal_history(path, "QQQ", since="2026-09-05T00:00:00+00:00")
    assert [row["timestamp"][:10] for row in rows] == ["2026-09-10", "2026-09-20"]
    # no row cap when a window is given (the old default capped at 2,000)
    assert len(storage.get_signal_history(path, "QQQ", limit=1)) == 1
    assert len(storage.get_signal_history(path, "QQQ", limit=1, since="2000-01-01")) == 3


# --- WAL mode -------------------------------------------------------------------

def test_init_db_puts_the_database_in_wal_mode_for_good(tmp_path):
    import sqlite3
    path = make_temp_db(tmp_path)
    with sqlite3.connect(path) as conn:           # a fresh connection sees it
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_read_in_progress_no_longer_blocks_the_worker_writing(tmp_path):
    # the API reads constantly; in rollback-journal mode an open read made the
    # worker's commit fail with "database is locked" once its wait ran out
    import sqlite3
    path = make_temp_db(tmp_path)
    storage.ensure_account(path, 10000.0)
    reader = sqlite3.connect(path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT balance FROM account").fetchone()   # read txn held open
        writer = sqlite3.connect(path, timeout=0.2)
        writer.execute("UPDATE account SET balance = 5000 WHERE id = 1")
        writer.commit()                                            # would raise pre-WAL
        writer.close()
        # the reader keeps its consistent snapshot until it finishes
        assert reader.execute("SELECT balance FROM account").fetchone()[0] == 10000.0
        reader.execute("COMMIT")
    finally:
        reader.close()
    assert storage.get_balance(path) == 5000.0


def test_backup_is_one_self_contained_file_with_everything_committed(tmp_path):
    import sqlite3
    path = make_temp_db(tmp_path)
    pinned = sqlite3.connect(path)       # keeps the log from being folded back on close
    try:
        storage.ensure_account(path, 1234.0)   # committed, but still in the -wal file
        target = storage.backup_db(path, tmp_path / "backups")
    finally:
        pinned.close()
    with sqlite3.connect(target) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT balance FROM account").fetchone()[0] == 1234.0
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]
