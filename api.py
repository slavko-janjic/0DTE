"""FastAPI backend for the 0DTE web UI.

Thin by design: every route is a few lines that call `webapi.payloads` (reads,
SQLite only) or `webapi.live` (writes and anything needing a live quote). The
domain logic lives where it already did - storage/, analytics/, paper_trading/,
signals/ - and worker.py is untouched: it keeps writing SQLite on its own
schedule, and this process only ever reads what it wrote.

Run it the way the scheduled task does:

    python -m uvicorn api:app --host 0.0.0.0 --port 8501

The frontend in webui/ is served from the same origin, so no CORS is needed.
There is no login: keep this on Tailscale/LAN.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import load_settings
from storage import db as storage
from webapi import live, payloads

log = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).parent / "webui"

config = load_settings()
# ZERODTE_DB_PATH lets a second instance (or a test) run against a copy of the
# database without touching config/settings.yaml - the worker's path is the one
# in the config, and nothing here should be able to move it by accident.
db_path = os.environ.get("ZERODTE_DB_PATH") or config["database"]["path"]

# Same first-run bootstrap the Streamlit app did, so pointing the scheduled task
# at this process is a drop-in swap.
storage.init_db(db_path)
storage.ensure_account(db_path, config["account"]["starting_balance"])
storage.ensure_worker_settings(db_path, config["poll_interval_minutes"] * 60)
storage.ensure_autopilot(db_path, default_enabled=True)
storage.ensure_calibration(db_path)

app = FastAPI(title="0DTE Console", docs_url="/api/docs", openapi_url="/api/openapi.json")


def _ticker(ticker: str) -> str:
    ticker = ticker.upper()
    if ticker not in config["tickers"]:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker}")
    return ticker


# --- reads ----------------------------------------------------------------

@app.get("/api/overview")
def get_overview() -> dict:
    """Header data: sentiment strip, wallet, worker health, autopilot, clock."""
    return payloads.overview(db_path, config)


@app.get("/api/signal/{ticker}")
def get_signal(ticker: str) -> dict:
    return payloads.signal_payload(db_path, config, _ticker(ticker))


@app.get("/api/signal/{ticker}/history")
def get_signal_history(
    ticker: str,
    range: str = Query("session", pattern="^(session|4h|3d|all)$"),
) -> dict:
    return payloads.signal_history(db_path, config, _ticker(ticker), range)


@app.get("/api/positions")
def get_positions() -> dict:
    """Open positions, re-priced from a live chain. The same profit-target /
    stop-loss rule the worker applies runs here too, so an exit fires on this
    poll cadence rather than waiting for the worker's next cycle."""
    quotes, auto_closed = live.refresh_open_positions(db_path, config)
    payload = payloads.positions_payload(db_path, config, quotes)
    payload["auto_closed"] = auto_closed
    return payload


@app.get("/api/history")
def get_history() -> dict:
    return payloads.trade_history(db_path, config)


@app.get("/api/calendar")
def get_calendar(year: int | None = None, month: int | None = None) -> dict:
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="month must be 1-12")
    return payloads.calendar_payload(db_path, config, year, month)


@app.get("/api/lab")
def get_lab() -> dict:
    return payloads.lab_payload(db_path, config)


@app.get("/api/cost/{ticker}")
def get_cost(ticker: str) -> dict:
    return payloads.cost_payload(db_path, config, _ticker(ticker))


@app.get("/api/autopilot")
def get_autopilot() -> dict:
    return payloads.autopilot_payload(db_path, config)


@app.get("/api/quote/{ticker}")
def get_quote(
    ticker: str,
    option_type: str = Query("call", pattern="^(call|put)$"),
    amount: float | None = None,
) -> dict:
    """What the Buy button would actually fill at right now (ask-side)."""
    return live.quote(_ticker(ticker), option_type, amount)


# --- writes ---------------------------------------------------------------

class TradeRequest(BaseModel):
    ticker: str
    option_type: str = Field(pattern="^(call|put)$")
    amount: float
    profit_target_pct: float | None = None
    stop_loss_pct: float | None = None


class TargetsRequest(BaseModel):
    profit_target_pct: float | None = None
    stop_loss_pct: float | None = None


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(off|day|continuous)$")


class BalanceRequest(BaseModel):
    balance: float


def _ok_or_400(result: dict) -> dict:
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Rejected"))
    return result


@app.post("/api/trade")
def post_trade(request: TradeRequest) -> dict:
    result = _ok_or_400(live.place_trade(
        db_path, config, _ticker(request.ticker), request.option_type, request.amount,
        request.profit_target_pct, request.stop_loss_pct,
    ))
    result["positions"] = payloads.positions_payload(db_path, config)
    return result


@app.post("/api/position/{position_id}/close")
def post_close(position_id: int) -> dict:
    result = _ok_or_400(live.close_position(db_path, position_id))
    result["positions"] = payloads.positions_payload(db_path, config)
    return result


@app.post("/api/position/{position_id}/targets")
def post_targets(position_id: int, request: TargetsRequest) -> dict:
    result = _ok_or_400(live.set_targets(
        db_path, position_id, request.profit_target_pct, request.stop_loss_pct))
    result["positions"] = payloads.positions_payload(db_path, config)
    return result


@app.post("/api/autopilot/mode")
def post_autopilot_mode(request: ModeRequest) -> dict:
    _ok_or_400(live.set_autopilot_mode(db_path, config, request.mode))
    return payloads.autopilot_payload(db_path, config)


@app.post("/api/wallet/balance")
def post_balance(request: BalanceRequest) -> dict:
    _ok_or_400(live.set_balance(db_path, request.balance))
    return payloads.wallet(db_path, config)


@app.post("/api/wallet/clear-history")
def post_clear_history() -> dict:
    live.clear_history(db_path)
    return payloads.trade_history(db_path, config)


# --- the frontend ---------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEBUI_DIR / "index.html")


# Mounted last so it can't shadow /api/*.
app.mount("/", StaticFiles(directory=WEBUI_DIR, html=True), name="webui")
