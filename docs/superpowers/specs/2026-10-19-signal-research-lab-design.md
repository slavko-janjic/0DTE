# Signal research lab — design

Date: 2026-10-19. Status: approved in conversation (approach A, candidate list as below);
the user handed over the remaining review steps ("take the wheel, ride it till it's done").

## Goal

Find out whether any of a short, pre-declared list of signals improves **the autopilot's
trade** — the 10:00 ET at-the-money 0DTE option on SPY/QQQ with the autopilot's exits —
over the blind trade. Judged on thousands of historical sessions instead of the ~70 the
app has recorded, with a test that the September review showed is needed: the result must
hold in every multi-year period separately, not only on average.

Out of scope: changing the live composite, worker or autopilot. A signal that passes gets
promoted to the live lab as a pre-registered shadow strategy in a separate change.

## What the user said vs. what is assumed

- Said: judge on the autopilot's trade; use free minute bars now; approach A; the
  candidate list below.
- Assumed: SPY and QQQ only (the autopilot's tickers); decision at 10:00 ET; the
  autopilot's current exit config (`shadow_strategies.autopilot_point.exit`).

## 1. Parts and data

| Part | Where | Does |
|---|---|---|
| History download | `data/history.py` | Alpaca free feed (`feed=sip`, split-adjusted), 1-minute bars 2016 → yesterday, regular hours only; cached as `{ticker}_{year}.csv.gz` under `<database dir>/history/`; only missing years/trailing days are fetched. Daily VIX open/close from Yahoo (`^VIX`). Plain `requests`, no new dependency. |
| Keys | `<database dir>/alpaca_keys.json` or env `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY` | Never in the repo. |
| Engine | `analytics/session_lab.py` | Pure functions: sessions from bars, signals at the decision minute, option-trade simulation, scoring, period test. |
| CLI | `research.py` (repo root, like `replay.py`) | `fetch`, `calibrate`, `validate-label`, `run`. Report-only except `fetch`, which writes only the cache. |
| Report | `SIGNAL_RESEARCH.md` | Dated results, like `SIGNAL_REVIEW.md`. |

The database dir is `Path(config.database_path(config)).parent` — outside OneDrive and
AppData (see the DB-location rule).

## 2. The outcome label: a simulated option trade

At 10:00 ET (minute 30) for each session and ticker:

1. Strike = the spot at the decision rounded to the nearest $1 (SPY/QQQ strikes).
2. Price a same-day ATM call and put with Black-Scholes (r = the app's
   `_RISK_FREE_RATE`), time to expiry = minutes to 16:00. Volatility = `k[ticker] ×
   VIX open / 100` (section 2a).
3. Entry fill = model price + half the ticker's typical spread; valuation each minute =
   model price − half spread (the app values open positions at the bid).
4. Walk the minute closes. Each minute, reprice, update the high-water mark, and call the
   app's own `paper_trading.engine.evaluate_exit` with the autopilot's exit config and
   `current_composite_score=None` (reversal exits need a live signal and are off for the
   autopilot anyway). Exit at the first reason; if none fires, the time cutoff fires at
   15:30.
5. Result per side: return % on premium (and the exit reason).

Each session yields `call_ret` and `put_ret`. **Null** = their average. A signal that picks
a side earns that side's return; its **excess** = its return − null.

### 2a. Calibration from the app's own records

- `k[ticker]`: for each recorded session (July 2026 onward), implied vol of the ATM call
  and put mids in `quote_snapshots` at 10:00–10:05 (Black-Scholes inversion, bisection),
  divided by that morning's VIX (`vix_snapshots`). `k` = median ratio.
- Spread: median ATM `ask − bid` in dollars at 10:00–10:05 per ticker.
- Both printed by `research.py calibrate`, written to the report, never hard-coded.

### 2b. Proxy check — hard stop

Re-simulate every closed SPY/QQQ shadow trade since July whose strategy has reversal
exits off (reversal 101) or whose real exit wasn't `signal_reversal`, from its actual entry
minute, strike and side, on the app's own minute spot path (`signal_snapshots.spot_price`),
with that strategy's exit config. Compare:

- **Pass:** exit reason class (target / stop / other) matches on ≥ 75% of trades AND the
  mean simulated return is within 5 percentage points of the mean real return.
- The app's option quotes come from Yahoo and lag the spot by up to ~15 minutes; the
  check is also run with the simulated option lagged 15 minutes, and both are reported.
- **Fail:** stop; no signal is run through a label that doesn't match reality.

Known simplifications: before 2022, same-day SPY/QQQ options didn't trade every day, so
the simulated option is synthetic there — it measures whether the direction call would have
paid on this structure. Constant intraday volatility (no smile, no intraday IV moves).

## 3. Candidate signals (pre-declared; nothing added after seeing results)

All computed from data available at 10:00: today's 09:30–09:59 bars, prior sessions, and
daily VIX through yesterday. Each returns a score in -1..1 (sign = direction) or None
(stand aside). `med_N(x)` = median of |x| over the previous 20 sessions (None until 20
exist). `r30` = (09:59 close − 09:30 open) / 09:30 open.

| # | Name | Score |
|---|---|---|
| 1 | `opening_drive` | clip(r30 / (2·med_N(r30))) |
| 2a | `gap_direction` | gap = (09:30 open − prior close) / prior close; clip(gap / (2·med_N(gap))) |
| 2b | `gap_behaviour` | on gap days only (\|gap\| ≥ med_N(gap)): the opening_drive score (continue whatever the first 30 minutes did with the gap — extend or fill); else None |
| 3a | `orb15` | range = 09:30–09:44 high/low; price = 09:59 close; above → clip((p−hi)/hi·100/0.2), below → clip((p−lo)/lo·100/0.2), inside → 0 |
| 3b | `technicals` | **benchmark**: the live `indicators.compute_technicals_score` on 09:30–09:59 resampled to 5-minute bars |
| 4a | `vwap` | `indicators.price_vs_vwap_score` on the session-so-far minute bars |
| 4b | `prior_levels` | above prior high → +1, below prior low → −1, else clip((p − prior close) / ((prior high − prior low)/2)) |
| 5 | `rel_strength` | z_T = r30_T / med_N(r30_T); score_T = clip((z_T − z_other)/2), other = the other ticker |
| 6 | `drive_by_vol` | rv = stdev of 09:30–09:59 1-min returns; ratio = rv / med_N(rv); score = opening_drive × (+1 if ratio ≥ 1 else −1) — follow on trend-like mornings, fade on quiet ones |
| 7 | `vix_change` | ch = (VIX close[t−1] − VIX close[t−2]) / VIX close[t−2]; clip(−ch / 0.10) |

A score of exactly 0 or None = stand aside that session. Each signal is tested in both
orientations (follow: trade sign(score); fade: trade −sign(score)) because September showed
the useful sign isn't known in advance: **10 signals × 2 = 20 tests**, and that count sets
the noise floor.

## 4. Evaluation and verdict

- **Observation unit: one per session** — the SPY and QQQ excess returns that day are
  averaged (the two are highly correlated; counting them separately would overstate the
  sample). Sessions where a signal stands aside on both tickers are skipped.
- **Periods:** 2016–2018, 2019–2021, 2022–2024, 2025–present.
- **PASS** requires all of:
  1. pooled t-stat of daily excess ≥ the noise floor √(2·ln 20) ≈ 2.45;
  2. mean excess positive in all four periods;
  3. mean net return (not excess) positive in 2022–present — the period with real daily
     0DTE options — i.e. it beats costs, not just the coin flip.
- **INCONCLUSIVE:** criterion 1 or 2 met but not all three. **FAIL:** otherwise.
- Reported per test: sessions traded, mean excess and t per period, pooled t, win rate,
  mean net return. Also reported, descriptive only (not part of the verdict): the same
  rows restricted to |score| ≥ 0.5.
- **Promotion (separate change):** a PASS becomes a pre-registered shadow strategy computed
  live; it joins the composite only after holding in the live lab.

## 5. Errors and testing

- Network/keys: `fetch` fails loudly naming the missing key file/env vars; a failed year
  is not cached; partial pages are not written.
- Sessions with missing minutes around the decision (no 09:30 open, no 09:59 close, fewer
  than 300 bars), half-days, and the first 20 sessions (no medians) are skipped and counted
  in the report.
- Unit tests (no network) for: bar → session grouping in ET incl. DST; each signal on
  hand-built sessions; Black-Scholes price and IV inversion round-trip; the trade simulator
  hitting target/stop/trailing/time; the label's null/excess arithmetic; per-period verdict
  logic incl. the noise floor; Alpaca response parsing and pagination with a fake session.

## Build order

1. Calibration + label simulator + proxy check on the app's own data (no keys needed).
2. Alpaca download + cache (needs the user's free Alpaca keys).
3. Signals + evaluation + CLI `run`.
4. Run, write `SIGNAL_RESEARCH.md`, PR.
