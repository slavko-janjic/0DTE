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
