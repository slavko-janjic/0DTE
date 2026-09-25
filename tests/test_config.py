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
