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
4. In the app, Autopilot → **Notifications: on**, allow the prompt, then **Send test
   alert**.

If the notification toggle can't turn on, it says why (not HTTPS, or not installed
on iPhone).

## Notes

- The worker only does anything useful during US market hours (`config/settings.yaml` -> `market_hours`); outside those hours it sleeps.
- All signal weights, exit-rule thresholds, position sizing, and poll interval are in `config/settings.yaml` - tune freely, no code changes needed.
- Sentiment sources (ApeWisdom, StockTwits, Reddit's public search) are all free and need no account or API key. Reddit's public search is currently blocked by their bot-protection more often than not - it's kept as best-effort and the signal degrades gracefully (ApeWisdom + StockTwits) when it's unavailable.
- The `prediction_markets` signal comes from Kalshi's daily S&P 500 / Nasdaq range markets (free, no key) - a crowd-money implied probability of the index closing above the current level. Readings can look noisy outside market hours or when the nearest event has thin/sparse strikes (e.g. weekends) - that's a real characteristic of the underlying data, not a bug.
- The `volatility_regime` signal comes from VIX, VIX9D, and VVIX (free via yfinance, no key). VIX9D above VIX (backwardation) signals near-term stress and leans bearish; contango and low VVIX lean bullish/calm. This is a market-wide "fear gauge" overlay, not ticker-specific - it reads the same for QQQ and SPY.
- The `trump_news` signal scans GDELT's free news API (no key) each cycle for recent headlines mentioning Trump alongside tariffs/trade/economy/Fed, then scores the tone with a simple bearish/bullish keyword lexicon. It's market-wide (same reading for QQQ and SPY), and only as good as headline volume that hour - quiet news cycles read as neutral (0.0), not missing data. Look-back window is `trump_news_lookback_hours` in `config/settings.yaml`.
- **Auto-pilot** (toggle in the Place-a-trade card, off by default): the worker automatically opens paper positions on high-confidence signals and attaches a stop/target, with guard rails - entry window (skips first 30 / last 60 min), never stacks on an existing position in a ticker, caps on concurrent auto positions and auto trades per day, a per-ticker cooldown after any close, and a daily circuit breaker on realized auto losses. All tunables live under `autopilot:` in `config/settings.yaml`; the on/off switch lives in the DB so it takes effect on the worker's next cycle without a restart. Manual trading keeps working regardless; positions are tagged auto/manual.
- The web UI updates over server-sent events (`/api/stream`): the server watches the database and pushes a nudge within a few seconds of anything changing, so a trade or a new signal shows up without waiting. A slow poll runs underneath as a fallback, and if the stream can't connect the UI falls back to polling every 12 seconds. Either way a backgrounded tab stops asking.
- **Tuning page**: everything the old Streamlit calibration expander had - the auto-calibrate switch, what it has adjusted recently, confidence calibration (predicted vs observed), per-signal accuracy, accuracy split by time of day / volatility / signal persistence, and the per-ticker weight suggestion with apply/revert. Per-ticker tables follow the ticker selected in the top strip.
- **Autopilot alerts**: turn on sound and/or browser notifications on the Autopilot page to get a heads-up the moment a ticker ARMS or the bot opens a position - in time to mirror it by hand. Both settings are remembered per browser; the browser only allows sound after you've clicked something on the page, which is what the toggle does.
- There is no login. Keep the UI on Tailscale or your LAN.
- `python -m pytest` covers the API helpers too (`tests/test_webapi.py`, `tests/test_api_endpoints.py`) - the endpoint tests need no network.
- This is a paper-trading / educational tool only. No real orders are ever placed.
