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

In another terminal, start the dashboard:

```
streamlit run dashboard.py --server.address 0.0.0.0
```

Streamlit will print a local URL (e.g. `http://192.168.1.x:8501`) - open that from your phone while on the same WiFi to confirm it works before setting up remote access.

## 3. Phone access from anywhere (Tailscale)

1. Install Tailscale on your PC: https://tailscale.com/download (sign in with any account - free tier is enough for personal use).
2. Install the Tailscale app on your phone and sign in with the same account.
3. Once both are connected, Tailscale assigns your PC a stable private IP (e.g. `100.x.x.x`) shown in the Tailscale app/admin console.
4. On your phone, browse to `http://<that-tailscale-ip>:8501` - this works over cellular data too, without exposing the dashboard publicly.

## Notes

- The worker only does anything useful during US market hours (`config/settings.yaml` -> `market_hours`); outside those hours it sleeps.
- All signal weights, exit-rule thresholds, position sizing, and poll interval are in `config/settings.yaml` - tune freely, no code changes needed.
- Sentiment sources (ApeWisdom, StockTwits, Reddit's public search) are all free and need no account or API key. Reddit's public search is currently blocked by their bot-protection more often than not - it's kept as best-effort and the signal degrades gracefully (ApeWisdom + StockTwits) when it's unavailable.
- The `prediction_markets` signal comes from Kalshi's daily S&P 500 / Nasdaq range markets (free, no key) - a crowd-money implied probability of the index closing above the current level. Readings can look noisy outside market hours or when the nearest event has thin/sparse strikes (e.g. weekends) - that's a real characteristic of the underlying data, not a bug.
- The `volatility_regime` signal comes from VIX, VIX9D, and VVIX (free via yfinance, no key). VIX9D above VIX (backwardation) signals near-term stress and leans bearish; contango and low VVIX lean bullish/calm. This is a market-wide "fear gauge" overlay, not ticker-specific - it reads the same for QQQ and SPY.
- The `trump_news` signal scans GDELT's free news API (no key) each cycle for recent headlines mentioning Trump alongside tariffs/trade/economy/Fed, then scores the tone with a simple bearish/bullish keyword lexicon. It's market-wide (same reading for QQQ and SPY), and only as good as headline volume that hour - quiet news cycles read as neutral (0.0), not missing data. Look-back window is `trump_news_lookback_hours` in `config/settings.yaml`.
- **Auto-pilot** (toggle in the Place-a-trade card, off by default): the worker automatically opens paper positions on high-confidence signals and attaches a stop/target, with guard rails - entry window (skips first 30 / last 60 min), never stacks on an existing position in a ticker, caps on concurrent auto positions and auto trades per day, a per-ticker cooldown after any close, and a daily circuit breaker on realized auto losses. All tunables live under `autopilot:` in `config/settings.yaml`; the on/off switch lives in the DB so it takes effect on the worker's next cycle without a restart. Manual trading keeps working regardless; positions are tagged auto/manual.
- The poll interval is also adjustable live from the dashboard's sidebar, no restart required.
- This is a paper-trading / educational tool only. No real orders are ever placed.
