"""Routing-level checks for api.py: the wiring around the payload helpers
(validation, 404s, static serving). Runs against an empty temp database via
ZERODTE_DB_PATH, and touches no market data - every endpoint exercised here
reads SQLite only.
"""
import importlib
import os

import pytest

pytest.importorskip("httpx", reason="httpx backs fastapi's TestClient")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    previous = os.environ.get("ZERODTE_DB_PATH")
    os.environ["ZERODTE_DB_PATH"] = str(tmp_path_factory.mktemp("api") / "api.db")
    import api
    importlib.reload(api)          # pick up the env var on a fresh import
    try:
        yield TestClient(api.app)
    finally:
        if previous is None:
            os.environ.pop("ZERODTE_DB_PATH", None)
        else:
            os.environ["ZERODTE_DB_PATH"] = previous


def test_overview_has_everything_the_header_needs(client):
    body = client.get("/api/overview").json()
    assert set(body) >= {"tickers", "wallet", "worker", "autopilot", "market"}
    assert body["worker"]["status"] == "never"      # no worker has run against this DB
    assert body["wallet"]["balance"] > 0


def test_unknown_ticker_is_a_404(client):
    assert client.get("/api/signal/NOTATICKER").status_code == 404
    assert client.get("/api/cost/NOTATICKER").status_code == 404


def test_history_range_is_validated(client):
    assert client.get("/api/signal/QQQ/history?range=session").status_code == 200
    assert client.get("/api/signal/QQQ/history?range=decade").status_code == 422


def test_calendar_rejects_an_impossible_month(client):
    assert client.get("/api/calendar?year=2026&month=13").status_code == 400


def test_autopilot_mode_round_trips(client):
    assert client.post("/api/autopilot/mode", json={"mode": "turbo"}).status_code == 422
    body = client.post("/api/autopilot/mode", json={"mode": "continuous"}).json()
    assert body["mode"] == "continuous"
    assert client.get("/api/autopilot").json()["mode"] == "continuous"


def test_targets_on_a_missing_position_is_a_400(client):
    response = client.post("/api/position/999/targets",
                           json={"profit_target_pct": 50, "stop_loss_pct": 35})
    assert response.status_code == 400


def test_frontend_is_served_from_the_same_origin(client):
    assert "0DTE Console" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_calibration_endpoint_serves_the_tuning_page(client):
    body = client.get("/api/calibration/QQQ").json()
    assert body["ticker"] == "QQQ"
    assert body["enabled"] is True
    assert set(body["context"]) == {"time_of_day", "volatility", "streak"}
    assert client.get("/api/calibration/NOTATICKER").status_code == 404


def test_calibration_toggle_and_revert(client):
    assert client.post("/api/calibration/enabled", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/api/calibration/QQQ").json()["enabled"] is False
    client.post("/api/calibration/enabled", json={"enabled": True})
    assert client.post("/api/calibration/revert", json={}).json() == {"ok": True}


def test_weights_action_is_validated(client):
    assert client.post("/api/weights/QQQ", json={"action": "sideways"}).status_code == 422
    # no graded history in this empty DB, so applying is refused with a reason
    response = client.post("/api/weights/QQQ", json={"action": "apply"})
    assert response.status_code == 400
    assert "graded history" in response.json()["detail"]
    assert client.post("/api/weights/QQQ", json={"action": "revert"}).status_code == 200


def test_overview_carries_the_alert_state(client):
    autopilot = client.get("/api/overview").json()["autopilot"]
    assert autopilot["armed"] == []
    assert autopilot["auto_open"] == []


def test_stream_route_is_registered(client):
    """The SSE stream itself is exercised against a real server (a never-ending
    response deadlocks TestClient's portal on teardown); here we only assert the
    route exists, so a rename can't silently drop it."""
    import api
    assert any(getattr(route, "path", "") == "/api/stream" for route in api.app.routes)


def test_startup_pins_the_worker_cadence_to_one_minute(client):
    """Parity with dashboard.py, which forced 60s on every load: without it a
    fresh database keeps config's 5-minute default after the cutover."""
    import api
    from storage import db as storage
    assert storage.get_poll_interval_seconds(api.db_path) == 60


def test_push_key_and_subscription_lifecycle(client):
    pytest.importorskip("pywebpush")
    info = client.get("/api/push/key").json()
    assert info["available"] is True and info["public_key"]

    assert client.post("/api/push/test", json={}).status_code == 400   # no devices yet
    bad = {"subscription": {"endpoint": "http://insecure", "keys": {}}}
    assert client.post("/api/push/subscribe", json=bad).status_code == 400

    good = {"subscription": {"endpoint": "https://push.example.test/device-1",
                             "keys": {"p256dh": "key", "auth": "secret"}}}
    assert client.post("/api/push/subscribe", json=good).json()["devices"] == 1
    assert client.get("/api/push/key").json()["devices"] == 1
    response = client.post("/api/push/unsubscribe",
                           json={"endpoint": "https://push.example.test/device-1"})
    assert response.json()["devices"] == 0
