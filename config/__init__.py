from pathlib import Path

import yaml

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"

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
