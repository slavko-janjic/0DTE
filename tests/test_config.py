"""The poll cadence comes from config - settings.yaml's value used to be dead,
overridden by a hard-coded 60 s in the web API."""
from config import load_settings, poll_interval_seconds


def test_poll_interval_reads_seconds():
    assert poll_interval_seconds({"poll_interval_seconds": 90}) == 90


def test_poll_interval_still_understands_the_legacy_minutes_key():
    assert poll_interval_seconds({"poll_interval_minutes": 2}) == 120
    # the new key wins when both are present
    assert poll_interval_seconds({"poll_interval_seconds": 45, "poll_interval_minutes": 5}) == 45


def test_poll_interval_defaults_to_one_minute():
    assert poll_interval_seconds({}) == 60


def test_poll_interval_is_clamped_to_what_the_app_assumes():
    assert poll_interval_seconds({"poll_interval_seconds": 1}) == 15       # rate limits
    assert poll_interval_seconds({"poll_interval_seconds": 3600}) == 600   # streak gap


def test_shipped_settings_poll_once_a_minute():
    # the accuracy gates and streak strategies in settings.yaml assume this
    assert poll_interval_seconds(load_settings()) == 60


# --- where the data lives -------------------------------------------------------

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402

import config as config_mod  # noqa: E402
from config import (  # noqa: E402
    PROJECT_ROOT, DatabaseNotMigrated, backup_dir, check_database_location, database_path,
)


@pytest.fixture()
def no_db_override(monkeypatch):
    monkeypatch.delenv("ZERODTE_DB_PATH", raising=False)


def test_database_path_expands_home_and_resolves_relative_to_the_project(no_db_override):
    assert database_path({"database": {"path": "~/AppData/Local/0DTE/0dte.db"}}) == \
        str(Path.home() / "AppData" / "Local" / "0DTE" / "0dte.db")
    # relative = from the project root, not whatever directory a process started in
    assert database_path({"database": {"path": "storage/x.db"}}) == \
        str(PROJECT_ROOT / "storage" / "x.db")


def test_zerodte_db_path_overrides_the_config(monkeypatch):
    monkeypatch.setenv("ZERODTE_DB_PATH", "/tmp/copy.db")
    assert database_path({"database": {"path": "~/elsewhere.db"}}) == "/tmp/copy.db"


def test_backups_default_to_the_synced_project_folder():
    assert backup_dir({"database": {"path": "x"}}) == PROJECT_ROOT / "storage" / "backups"
    assert backup_dir({"database": {"backup_dir": "~/b"}}) == Path.home() / "b"


def test_shipped_settings_keep_the_live_db_out_of_the_project_folder(no_db_override):
    # the project folder is the OneDrive-synced one; the backups stay in it
    settings = load_settings()
    assert not Path(database_path(settings)).is_relative_to(PROJECT_ROOT)
    assert backup_dir(settings).is_relative_to(PROJECT_ROOT)


def test_shipped_settings_keep_the_live_db_out_of_appdata(no_db_override):
    # a packaged app's AppData writes are redirected per app, so a tool run from
    # inside one and the scheduled tasks would see different files - the first
    # move to AppData\Local\0DTE started the tasks on an empty database
    parts = {part.lower() for part in Path(database_path(load_settings())).parts}
    assert "appdata" not in parts


def test_refuses_to_orphan_the_legacy_database(tmp_path, no_db_override):
    legacy = tmp_path / "old" / "0dte.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"sqlite")
    target = tmp_path / "new" / "0dte.db"
    with pytest.raises(DatabaseNotMigrated, match="doesn't exist yet"):
        check_database_location(str(target), legacy_path=legacy)
    # once it's been copied across, starting is fine
    target.parent.mkdir()
    target.write_bytes(b"sqlite")
    check_database_location(str(target), legacy_path=legacy)


def test_no_legacy_database_means_a_fresh_start_is_fine(tmp_path, no_db_override):
    check_database_location(str(tmp_path / "new.db"), legacy_path=tmp_path / "absent.db")


def test_still_pointing_at_the_legacy_database_is_fine(tmp_path, no_db_override):
    legacy = tmp_path / "0dte.db"
    legacy.write_bytes(b"sqlite")
    check_database_location(str(legacy), legacy_path=legacy)


def test_guard_is_skipped_for_an_explicit_copy(tmp_path, monkeypatch):
    legacy = tmp_path / "0dte.db"
    legacy.write_bytes(b"sqlite")
    monkeypatch.setenv("ZERODTE_DB_PATH", str(tmp_path / "copy.db"))
    check_database_location(str(tmp_path / "copy.db"), legacy_path=legacy)


def test_legacy_path_is_the_old_in_project_location():
    assert config_mod.LEGACY_DB_PATH == PROJECT_ROOT / "storage" / "0dte.db"
