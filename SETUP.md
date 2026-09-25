# Setup

## 1. Install dependencies

```
python -m pip install -r requirements.txt
```

## 2. Run the two processes

In one terminal, start the background worker (polls data, updates signals and positions):

```
python worker.py
```

In another terminal, start the web UI (FastAPI serves both the JSON API and the
frontend in `webui/`):

```
python -m uvicorn api:app --host 0.0.0.0 --port 8501
```

Open `http://localhost:8501` (or `http://192.168.1.x:8501` from your phone on the
same WiFi) to confirm it works before setting up remote access.

The old Streamlit dashboard is still in the repo as a fallback for one cycle:

```
streamlit run dashboard.py --server.address 0.0.0.0
```

Both read the same SQLite database, so they agree on every number - but run only
one at a time on port 8501.

To point the API at a copy of the database (e.g. to poke at it without touching
the live one), set `ZERODTE_DB_PATH`.

### Optional: require a token

There is no login by default - keep the UI on Tailscale or your LAN. If you want
a second lock on it, set a shared secret before starting the app:

```
$env:ZERODTE_TOKEN = "some-long-random-string"     # PowerShell, this session
setx ZERODTE_TOKEN "some-long-random-string"       # ...or persist it for the scheduled task
```

Then open `http://<host>:8501/?token=some-long-random-string` once on each
device - the token is stored in a cookie, so the link only has to be used the
first time. Unset the variable to turn it off again.

## 3. Phone access from anywhere (Tailscale)

1. Install Tailscale on your PC: https://tailscale.com/download (sign in with any account - free tier is enough for personal use).
2. Install the Tailscale app on your phone and sign in with the same account.
3. Once both are connected, Tailscale assigns your PC a stable private IP (e.g. `100.x.x.x`) shown in the Tailscale app/admin console.
4. On your phone, browse to `http://<that-tailscale-ip>:8501` - this works over cellular data too, without exposing the dashboard publicly.

## 4. Phone alerts: HTTPS + home-screen app (optional)

The ARMED/OPENED **sound** works from any address. Browser **notifications** need
HTTPS, and `http://100.x.x.x:8501` isn't. Tailscale can put a real certificate in
front of the app without exposing it outside your tailnet:

1. In the Tailscale admin console → **DNS**, make sure MagicDNS is on and click
   **Enable HTTPS** (one-time).
2. On the PC (Tailscale 1.52 or newer):
   ```
   tailscale serve --bg 8501
   ```
   It prints an address like `https://your-pc.tail1234.ts.net`. That's the one to
   use on the phone from now on. `--bg` keeps it running across reboots; undo with
   `tailscale serve --https=443 off`.
3. On the phone, open that address and install it:
   - **iPhone:** Share → **Add to Home Screen**, then open it from the home-screen
     icon. iOS only allows notifications for an installed web app.
   - **Android (Chrome):** menu → **Install app** (or Add to Home screen).
4. In the app, Autopilot → **Notifications**, allow the prompt, then **Send test
   alert**. The button tells you which kind you got:
   - **on · push**: alerts come through Apple's/Google's push service, so they reach
     the phone **with the app closed and the screen locked**. The server spots
     ARMED/OPENED itself (checking every 15 s) and sends to every device that
     turned notifications on. The test alert takes the same route, so if it
     arrives, the whole chain works.
   - **on · while open**: push couldn't be set up on this device; alerts still pop
     up, but only while the app is running.

If the toggle can't turn on at all, it says why (not HTTPS, or not installed on
iPhone). Push needs the `pywebpush` package (in `requirements.txt`). The server
generates its push key on first start as `vapid_private.pem`, beside the database
(`%USERPROFILE%\0DTE-data\` by default - see below). If that file is deleted, each
phone just has to turn notifications on again.

### Where the data lives

The live database is at `%USERPROFILE%\0DTE-data\0dte.db` (`database.path` in
`config/settings.yaml`), deliberately **outside** the OneDrive-synced project
folder: it's written every minute, and OneDrive locking it mid-write can fail a
commit. Nightly backups go to `storage/backups/` (`database.backup_dir`), which
*is* synced - each backup is written once and never touched again, so OneDrive
copies them safely.

Don't put it under `AppData`: Windows redirects a packaged app's AppData writes
into a private per-app folder, so a tool run from inside one (e.g. the Claude
desktop app) and the scheduled tasks would each see a different file.

If the worker or dashboard refuses to start with "the database is configured at
… which doesn't exist yet, but the old one is still at …", the database hasn't
been moved yet: stop both tasks, copy `storage/0dte.db` and
`storage/vapid_private.pem` to `%USERPROFILE%\0DTE-data\`, rename the old
`0dte.db`, then start the tasks again. Check through the web app (balance,
worker pid) that the tasks really use the new file before renaming anything.

## Notes

- The worker only does anything useful during US market hours (`config/settings.yaml` -> `market_hours`); outside those hours it sleeps.
- All signal weights, exit-rule thresholds, position sizing, and poll interval are in `config/settings.yaml` - tune freely, no code changes needed.
- The composite blends three signals, all from free yfinance data with no key (the sentiment, IV-skew, prediction-market and news signals were removed in September 2026 - see `SIGNAL_REVIEW.md`):
  - `technicals` - from 5-minute bars, the average of: price vs. VWAP, 30-minute momentum, RSI, and position against the opening range.
  - `order_flow` - the day's call vs. put volume on the 0DTE chain, plus where max pain sits relative to spot.
  - `volatility_regime` - VIX, VIX9D and VVIX, each scored against its own recent median (contango is the normal state, so the level alone isn't directional). Market-wide, not ticker-specific - it reads the same for QQQ and SPY.
- **Autopilot** (Autopilot page: off / day session / continuous): the worker opens paper positions on confident signals and attaches a stop/target, with guard rails - a decision window after the open, never stacking on an existing position in a ticker, caps on concurrent auto positions and auto trades per day, a per-ticker cooldown after any close, a daily circuit breaker on realized auto losses, standing down around scheduled catalysts, and a confidence gate on the calibrated band's lower bound. Each trade risks `risk_per_trade_pct` of the balance, so a small balance can't afford a contract. All tunables live under `autopilot:` in `config/settings.yaml`; the mode lives in the DB, so it takes effect on the worker's next cycle without a restart. Manual trading keeps working regardless; positions are tagged auto/manual.
- The web UI updates over server-sent events (`/api/stream`): the server watches the database and pushes a nudge within a few seconds of anything changing, so a trade or a new signal shows up without waiting. A slow poll runs underneath as a fallback, and if the stream can't connect the UI falls back to polling every 12 seconds. Either way a backgrounded tab stops asking.
- **Tuning page**: everything the old Streamlit calibration expander had - the auto-calibrate switch, what it has adjusted recently, confidence calibration (predicted vs observed), per-signal accuracy, accuracy split by time of day / volatility / signal persistence, and the per-ticker weight suggestion with apply/revert. Per-ticker tables follow the ticker selected in the top strip.
- **Autopilot alerts**: turn on sound and/or browser notifications on the Autopilot page to get a heads-up the moment a ticker ARMS or the bot opens a position - in time to mirror it by hand. Both settings are remembered per browser; the browser only allows sound after you've clicked something on the page, which is what the toggle does.
- There is no login. Keep the UI on Tailscale or your LAN.
- `python -m pytest` covers the API helpers too (`tests/test_webapi.py`, `tests/test_api_endpoints.py`) - the endpoint tests need no network.
- This is a paper-trading / educational tool only. No real orders are ever placed.
