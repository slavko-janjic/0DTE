import os
from pathlib import Path

import yaml

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Where the database lived until 2026-09-25 - inside the OneDrive-synced
# project folder. Kept only to catch a start-up that would otherwise orphan it.
LEGACY_DB_PATH = PROJECT_ROOT / "storage" / "0dte.db"
DEFAULT_BACKUP_DIR = "storage/backups"


class DatabaseNotMigrated(RuntimeError):
    """The configured database doesn't exist yet but the legacy one does."""


def _resolve(raw: str) -> Path:
    """~ and environment variables expanded; relative paths are taken from the
    project root (not the working directory), so every process agrees."""
    path = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    return path if path.is_absolute() else PROJECT_ROOT / path


def database_path(config: dict) -> str:
    """The SQLite database every process uses: ZERODTE_DB_PATH if set (a test,
    or a second instance on a copy), else config database.path."""
    override = os.environ.get("ZERODTE_DB_PATH")
    if override:
        return override
    return str(_resolve(config["database"]["path"]))


def backup_dir(config: dict) -> Path:
    """Where the nightly backups go (config database.backup_dir). Separate from
    the database on purpose: backups are written once and never touched, so
    they can live in a synced folder the live database mustn't."""
    return _resolve(config.get("database", {}).get("backup_dir", DEFAULT_BACKUP_DIR))


def check_database_location(db_path: str, legacy_path: Path = LEGACY_DB_PATH) -> None:
    """Refuse to start on a database that doesn't exist yet while the legacy
    one still does: init_db would quietly create an empty one (a fresh balance,
    no history) and every process would carry on as if nothing happened.
    Raises DatabaseNotMigrated with what to do. Skipped under ZERODTE_DB_PATH."""
    if os.environ.get("ZERODTE_DB_PATH"):
        return
    target = Path(db_path)
    if target.exists() or not legacy_path.exists():
        return
    if target.resolve() == legacy_path.resolve():
        return
    raise DatabaseNotMigrated(
        f"The database is configured at {target}, which doesn't exist yet, but the old "
        f"one is still at {legacy_path}. Stop the worker and dashboard, copy it (and "
        f"vapid_private.pem beside it) to the new location, then rename the old file - "
        f"starting now would create an empty database instead.")

DEFAULT_POLL_INTERVAL_SECONDS = 60
# Faster than this risks Yahoo rate-limiting. Slower than 10 minutes breaks
# direction-streak tracking, which treats a gap longer than
# storage.db.STREAK_MAX_GAP_MINUTES (10) between polls as a fresh start.
MIN_POLL_INTERVAL_SECONDS = 15
MAX_POLL_INTERVAL_SECONDS = 600


def load_settings(path: Path | str = _SETTINGS_PATH) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def poll_interval_seconds(config: dict) -> int:
    """The worker's poll cadence from config - the single source of truth.
    Reads poll_interval_seconds (or a legacy poll_interval_minutes), clamped
    to the range the rest of the app assumes."""
    if "poll_interval_seconds" in config:
        seconds = config["poll_interval_seconds"]
    elif "poll_interval_minutes" in config:
        seconds = config["poll_interval_minutes"] * 60
    else:
        seconds = DEFAULT_POLL_INTERVAL_SECONDS
    return int(max(MIN_POLL_INTERVAL_SECONDS, min(MAX_POLL_INTERVAL_SECONDS, seconds)))
