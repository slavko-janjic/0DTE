# Setup

## 1. Install dependencies

```
python -m pip install -r requirements.txt
```

## 2. Reddit API credentials (for PRAW)

1. Go to https://www.reddit.com/prefs/apps and click "create app" (or "create another app").
2. Choose type **script**, name it anything (e.g. `0dte-paper-trading`), leave the redirect URI as `http://localhost:8080`.
3. After creating it, note the client ID (under the app name) and the client secret.
4. Set these as environment variables before running the worker (PowerShell):

```powershell
$env:REDDIT_CLIENT_ID = "your-client-id"
$env:REDDIT_CLIENT_SECRET = "your-client-secret"
$env:REDDIT_USER_AGENT = "0dte-paper-trading-tool by u/your-username"
```

If these aren't set, the tool still works - Reddit context is simply skipped, and ApeWisdom/StockTwits keep providing sentiment.

## 3. Run the two processes

In one terminal, start the background worker (polls data, updates signals and positions):

```
python worker.py
```

In another terminal, start the dashboard:

```
streamlit run dashboard.py --server.address 0.0.0.0
```

Streamlit will print a local URL (e.g. `http://192.168.1.x:8501`) - open that from your phone while on the same WiFi to confirm it works before setting up remote access.

## 4. Phone access from anywhere (Tailscale)

1. Install Tailscale on your PC: https://tailscale.com/download (sign in with any account - free tier is enough for personal use).
2. Install the Tailscale app on your phone and sign in with the same account.
3. Once both are connected, Tailscale assigns your PC a stable private IP (e.g. `100.x.x.x`) shown in the Tailscale app/admin console.
4. On your phone, browse to `http://<that-tailscale-ip>:8501` - this works over cellular data too, without exposing the dashboard publicly.

## Notes

- The worker only does anything useful during US market hours (`config/settings.yaml` -> `market_hours`); outside those hours it sleeps.
- All signal weights, exit-rule thresholds, position sizing, and poll interval are in `config/settings.yaml` - tune freely, no code changes needed.
- This is a paper-trading / educational tool only. No real orders are ever placed.
