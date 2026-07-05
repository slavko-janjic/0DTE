# 0DTE Paper Trading Tool — Project Brief

## Goal
A tweakable paper trading tool for estimating 0DTE option movement (focus: QQQ, SPY, and similar), combining technicals, greeks/IV, order flow, and social sentiment into a single signal.

## Tickers (focus)
- **QQQ** — primary focus (Nasdaq-100, higher beta, tech catalysts)
- **SPY** — most liquid, penny spreads, benchmark
- Optional: IWM, TSLA, NVDA (later, once v1 works)

## Signal components (theory from chat)
1. **Technicals / momentum** — intraday price action, key levels
2. **Greeks & IV** — gamma, theta decay, IV crush
3. **Order flow** — volume by strike, gamma walls, max pain
4. **Social sentiment** — Reddit + X/Twitter (must-have from v1, not nice-to-have)

## Data sources (free)
- **yfinance** (Python, no API key) — prices, option chains, IV, volume/OI
  - Note: delayed data, greeks not always precise → good enough for prototype
- **PRAW** (Reddit API, free) — sentiment from relevant subreddits (e.g. r/options, r/wallstreetbets)
- **X/Twitter** — check current free-tier API limits at implementation time; if too restrictive, fall back to an alternative or drop X from v1 with a clear note

## Architecture (proposed modules)
```
0DTE/
├── data/           # yfinance wrapper — prices, option chains, IV
├── sentiment/      # Reddit (PRAW) + X sentiment scraping/scoring
├── signals/        # combines all inputs into a composite score (weights, config)
├── paper_trading/  # entry/exit simulation, P&L tracking
├── config/         # tweakable parameters (tickers, weights, thresholds)
└── main.py         # entry point / orchestrator
```

## Format
Local Python repo (not a web artifact) — needs real network access (scraping, API calls) and persistent state over time.

## Next step
Continue in **Claude Code** — set up a skeleton repo (folders, requirements.txt, empty modules), then fill in starting with the data + sentiment layer as a test.

## Open questions to resolve in the Code session
- Which subreddits / keywords for sentiment scoring?
- X/Twitter free tier — availability and limits at implementation time
- Concrete weights for the composite signal (start arbitrary, then tweak)
- Risk management rules for the paper trading engine (position sizing, max loss per trade)

## Disclaimer
This is an educational/theoretical project for paper trading (simulation). Not investment advice.
