# Web UI plan — make the dashboard exactly like the mockup

**Goal:** replace the Streamlit dashboard with a pixel-perfect UI matching the
design mockup, by putting a thin **FastAPI** backend over the *existing* Python
and using the **mockup HTML/CSS/JS as the real frontend**.

**Why this shape:** the domain logic is already cleanly decoupled
(`storage`, `analytics`, `paper_trading.engine`, `signals`, `data.market_data`,
`paper_trading.shadow`). So this is **plumbing, not a rewrite** — thin JSON
endpoints that call functions the Streamlit dashboard already calls, plus a
frontend that already exists. Streamlit caps at ~95% (no fixed bottom-nav, no
in-app theme toggle in v1.58, layout fights); a custom frontend is 100%.

**Starting assets already in the repo:**
- `webui/mockup.html` — the exact target UI (design mockup, Version 7). This is
  the frontend seed; wire its hardcoded example data to `fetch()` calls.
- `dashboard.py` — the CURRENT Streamlit app. Treat each of its section
  functions as the **spec for the matching endpoint's data** (they already
  compute exactly what each card needs). Keep it working as a fallback until
  cutover.
- Design reference (screenshots): Artifact "0DTE Console"
  https://claude.ai/artifact/MdoJwKPAGkH3tASupLWhG5

**Non-negotiables to preserve:** honest fills (entries fill ask-side via
`market_data.contract_entry_price`; positions valued bid-side), the
single-instance worker + heartbeat, autopilot guard rails, and the "market
closed / no data" states. Do NOT touch `worker.py` — it keeps writing SQLite.

---

## Architecture

```
worker.py  (UNCHANGED) ──writes──> storage/0dte.db (SQLite)
                                        │ reads
api.py (NEW, FastAPI/uvicorn) ──────────┘
   ├── serves webui/ (index.html + assets) as static files
   ├── GET  JSON endpoints (read)         -> existing storage/analytics/engine
   └── POST endpoints (place trade, close, targets, autopilot mode)
frontend (webui/, from mockup.html) --fetch()+poll(~12s)--> api.py
```

- **Process:** one `api.py` run by the `0DTE-Dashboard` scheduled task (replaces
  the `streamlit run` action; same port 8501, `--host 0.0.0.0`). Worker task
  unchanged. Remote access = Tailscale (see memory `ui-redesign-status` / earlier
  discussion); the app still has no login, so keep it private (Tailscale/LAN) or
  add a shared-secret token later.
- **Live updates:** start with **client polling** every ~12s (matches today's
  fragment cadence). Add SSE/WebSocket push only later if wanted.
- **Serving the frontend:** FastAPI `StaticFiles` mounts `webui/`; `/` returns
  `index.html`. Frontend calls same-origin `/api/...` (no CORS needed).

---

## Reused Python (source of truth per endpoint)

Map each endpoint to what the Streamlit section already does:

| Endpoint data | Existing code to reuse |
|---|---|
| Latest signal per ticker | `storage.get_latest_signal`, `_display_confidence` logic (calibrated vs raw), `gamma_regime`/`gamma_score`, `direction_streak`, `subscores_json`, `storage.effective_weights` |
| Sentiment strip | `get_latest_signal` for each `config["tickers"]`; direction+confidence |
| Wallet | `storage.get_balance`, `analytics.summarize_pnl(open, closed)` |
| Signal history + accuracy chart | `storage.get_signal_history`, `analytics.history_snapshots`, `evaluate_signal_accuracy`, `overall_accuracy_pct`, `daily_accuracy_summary`, `analytics.charting.*`, `signals.composite` |
| Day setup | `storage.get_day_setup` |
| Strategy lab | `storage.get_closed_shadow_positions`/`get_open_shadow_positions`, `paper_trading.shadow.strategy_scorecard`, `strategy_edge` |
| Cost of trading | `storage.get_quote_history`/`get_latest_quote`, `spread_summary`, `spread_by_minute_bucket`, `cheapest_windows` (same imports dashboard.py uses) |
| Open positions | `storage.get_open_positions`, live price via `market_data.find_contract_price` + `calculate_pnl`, `contract_spread_pct`; fast auto-close via `engine.price_target_exit` |
| Trade history | `storage.get_closed_positions` (add `opened_by` source tag) |
| Daily P&L calendar | `analytics.daily_realized_pnl`, `analytics.month_calendar_cells`, `engine.shift_month` |
| Autopilot intent | `engine.explain_auto_decision` per whitelisted ticker (already exposes lean/gate/blocker) + clock helpers from `worker` (`is_market_open`, `minutes_since_market_open`, `minutes_to_market_close`) + `signals.day_setup` catalyst helpers |
| Autopilot state/record/config | `storage.get_autopilot_state`, closed `opened_by='auto'` positions, `config["autopilot"]` |
| Place trade (POST) | `market_data.get_option_chain`+`find_atm_contract`+`contract_entry_price`, `engine.calculate_contracts`, `engine.buy` |
| Close position (POST) | `engine.close` |
| Set exit targets (POST) | `storage.set_position_exit_targets` |
| Set autopilot mode (POST) | `storage.set_autopilot_state` (+ day-session arming logic from `section_autopilot_controls`) |

> **Tip for the builder:** open `dashboard.py`'s `page_signals`, `section_*`,
> `render_*`, and `page_autopilot` functions — each is a ready-made recipe for
> the JSON an endpoint should return. Copy the computation, return a dict.

---

## Endpoint sketch

Read (GET, JSON):
- `GET /api/overview` — sentiment strip (all tickers: name/dir/confidence),
  wallet, worker health (heartbeat age -> up/down), autopilot mode. (One call
  the header/poll uses.)
- `GET /api/signal/{ticker}` — direction, confidence (calibrated+raw), gamma,
  streak, subscores+weights, day setup, live spot, freshness.
- `GET /api/signal/{ticker}/history?range=session|4h|3d|all` — points for the
  price+score chart and accuracy summary.
- `GET /api/positions` — open positions with live P&L + spread.
- `GET /api/history` — closed trades (+ source tag).
- `GET /api/calendar?year&month` — daily P&L cells.
- `GET /api/lab` — strategy leaderboard rows.
- `GET /api/cost/{ticker}` — spread table + intraday curve + cheapest windows.
- `GET /api/autopilot` — mode, intent per ticker, record, config, today's stats.

Write (POST, JSON body, return updated resource):
- `POST /api/trade` `{ticker, type, amount}` (ATM 0DTE; honest ask fill).
- `POST /api/position/{id}/close`
- `POST /api/position/{id}/targets` `{profit_target_pct, stop_loss_pct}`
- `POST /api/autopilot/mode` `{mode: off|day|continuous}`
- (settings, optional) `POST /api/wallet/balance`, clear history.

Concurrency: SQLite + short-lived connections (already how `storage.connect`
works). Single user, so fine. Make market-data calls resilient (try/except ->
"unavailable" rather than 500).

---

## Frontend wiring (from webui/mockup.html)

1. Strip the mockup-only bits: the amber "Mockup" banner, the demo down-state
   toggles, and hardcoded `DATA`/example numbers.
2. Add a small `api.js`: `get(path)`, `post(path, body)`, and a `poll(fn, ms)`
   helper. Render functions take JSON and fill the existing DOM.
3. Page routing already exists in the mockup (nav + `[hidden]` sections + bottom
   nav). Keep it; on page show, fetch that page's data; poll the visible page
   every ~12s + the header/overview always.
4. The 3-way theme toggle and mobile bottom-nav are ALREADY in the mockup and
   work — no Streamlit limitation now.
5. Loading + error + "market closed" states per card.

---

## Phases (each independently shippable on the branch)

1. **Scaffold** — `api.py` serves `webui/index.html` + `GET /api/signal/{ticker}`
   and `GET /api/overview`; wire the Signals signal card + sentiment strip +
   wallet to live data. Prove the mockup renders real QQQ.
2. **All reads** — remaining GET endpoints; wire every page's cards + the chart
   + calendar + lab + cost + autopilot intent. Polling in place.
3. **Writes** — the 5 POSTs; wire place-trade, close, targets, autopilot mode.
4. **Polish** — theme, mobile, loading/error/empty states, favicon/title.
5. **Cutover** — repoint `0DTE-Dashboard` task to `api.py` (uvicorn on 8501).
   Keep the Streamlit `dashboard.py` in the repo as a fallback for one cycle.
6. **Optional** — SSE/WebSocket push; shared-secret auth for remote.

---

## Cutover + rollback

- New task action (illustrative):
  `python -m uvicorn api:app --host 0.0.0.0 --port 8501`
  (reuse the python-direct + Limited + StopExisting scheduled-task setup already
  in `reregister_tasks.ps1`; just swap the dashboard action's command/args).
- Rollback = point the task back at `streamlit run dashboard.py`. Keep both
  entrypoints until the API version has run a full trading day cleanly.

## Testing

- Unit-test the endpoint helper functions (pure) the same way `tests/` covers
  the domain logic; the existing 333 tests already cover storage/analytics/engine
  and must stay green.
- Manual: click every button (place/close/targets/mode), confirm DB changes and
  the poll reflects them; verify market-closed + no-data states; check mobile.

## Dependencies

Add `fastapi`, `uvicorn[standard]` (and `httpx` for tests) to the environment.

## Which model

- **Opus** for: this plan's decisions, the `api.py` shape, live-update design,
  the write endpoints (side effects), auth, cutover. Fast mode (Opus) is great
  for the iterative frontend wiring.
- **Sonnet** for: the volume of read endpoints and the frontend `fetch` wiring
  once the shapes are fixed — fast/cheap on well-specified code.
- Skip Haiku/Fable for this (too integrated).

## Pointers

- Frontend seed: `webui/mockup.html`
- Data recipes: `dashboard.py` (section functions), `analytics/`, `storage/db.py`,
  `paper_trading/engine.py`, `data/market_data.py`, `paper_trading/shadow.py`
- Worker (do not change): `worker.py`
- Scheduled-task setup to adapt: `reregister_tasks.ps1`
- Design reference: https://claude.ai/artifact/MdoJwKPAGkH3tASupLWhG5
