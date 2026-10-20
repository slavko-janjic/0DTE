# Intraday + events research — design

Date: 2026-10-20. Status: approved in conversation ("Research, then lab"; all four event
families; the user handed over the rest: "take the wheel on this").
Builds on `2026-10-19-signal-research-lab-design.md` (same label, verdict rules, data).

## Goal

Does a direction signal improve the autopilot-style trade **later in the session** than
10:00, and do the morning edge or any later edge **hold or break by weekday and on
scheduled events**? Judged on 2016–2026 minute bars, with the test count fixed in advance.
Nothing live changes; passes become pre-registered lab strategies in a separate change.

## 1. Main tests (32, pre-declared)

- Decision times (minutes after the 09:30 open): **60 (10:30), 120 (11:30), 210 (13:00),
  270 (14:00)**. 10:00 was tested on 2026-10-19 and is not re-tested.
- Label at each time: the 2026-10-19 label (Black-Scholes ATM call and put, k × VIX open,
  calibrated half-spread, the app's `evaluate_exit` with the autopilot exit config) entered
  at that minute.
- Signals at decision minute m (price p = close of bar m−1; only data known then):

| Signal | Score |
|---|---|
| `trail30` | x = (p − p₋₃₀)/p₋₃₀; clip(x / (2·med)), med = median \|x\| at the same minute over the previous 20 complete sessions |
| `session_move` | x = (p − open)/open; same normalisation |
| `vwap` | `indicators.price_vs_vwap_score(p, vwap(bars 0..m−1))` |
| `orb15` | `session_lab.orb15_score(p, 09:30–09:44 high, low)` |

- 4 times × 4 signals × follow/fade = **32 tests**; noise floor √(2 ln 32) ≈ 2.63.
- Verdict per test exactly as 2026-10-19: PASS = t ≥ floor, positive excess in all four
  periods, net positive since 2022; one observation per day (SPY and QQQ averaged).

## 2. Slices (secondary)

Applied to every main-test PASS and to `gap_behaviour` (follow) at 10:00:

- Weekday (Mon–Fri) and 8 event tags: FOMC decision day, CPI release day, jobs-report day,
  monthly options expiry (3rd Friday, or the prior session if that's a holiday), quad
  witching (Mar/Jun/Sep/Dec expiry), last session of the month, last session of the quarter,
  first session after a mega-cap (NVDA, AAPL, MSFT, AMZN, GOOGL, META) report (amc → next
  session, bmo → same session).
- Per slice: days, mean excess in the slice vs. outside it, and Welch t of the difference.
  13 slices → slice noise floor √(2 ln 13) ≈ 2.26.
  - **too few days**: < 30 days in the slice;
  - **breaks**: slice mean ≤ 0 and difference t ≤ −2.26;
  - **holds**: slice mean > 0;
  - **weaker, not significant**: otherwise.
- "Breaks" slices become candidate stand-down filters (pre-registered, separate change).
- Descriptive only: the blind trade (coin flip) by decision time, weekday and event tag.
  Its net level is not trustworthy, least of all on event days (real 0DTE IV jumps before
  8:30 / 14:00 releases; the simulation prices off the VIX open).

## 3. Event calendar

- FOMC / CPI / jobs: official dates compiled from federalreserve.gov and bls.gov archives
  into `<data dir>/history/events_raw/{fomc,cpi,jobs}.csv` (helper agent, every row with its
  source URL), spot-checked before use.
- Earnings: `events_raw/earnings.csv` (ticker, date, amc/bmo) from Yahoo / company IR pages;
  SEC EDGAR only with the user's permission (it requires a contact email). Coverage gaps are
  reported and the earnings slice restricted to covered years.
- Options expiry, quad witching, month/quarter end: computed from the session calendar.

## 4. Build

- `analytics/intraday_lab.py`: signals at a decision minute, labels at a decision minute,
  event tags, slice tests (pure).
- `analytics/session_lab.label_session` gains a `decision_minute` parameter (default 30,
  unchanged behaviour).
- `research.py intraday [--report PATH]`.
- Unit tests for each signal's no-look-ahead, the label at a later minute, event tagging
  (opex holiday shift, quad, month/quarter end, earnings amc/bmo), and slice verdicts.
- Results appended to `SIGNAL_RESEARCH.md`.
