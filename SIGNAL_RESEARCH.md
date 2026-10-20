# Signal research — 2026-10-19

Does any of ten pre-declared signals improve **the autopilot's trade** (10:00 ET ATM
0DTE option on SPY/QQQ, autopilot exits) over the blind trade? Design:
`docs/superpowers/specs/2026-10-19-signal-research-lab-design.md`. Tools: `research.py`,
`analytics/option_sim.py`, `analytics/session_lab.py`, `data/history.py`.

**Status:** complete — calibration, label check (PASS) and the 2016→now run. One signal
passes; see Results.

## Finding 1: the app's option quotes are 15 minutes behind the share price

Correlation of 1-minute changes in the recorded ATM option price with the share-price
change N minutes earlier (`python research.py calibrate`, ~9,000 quote pairs per ticker):

| Lag | 0 | 5 | 10 | 14 | **15** | 16 | 20 |
|---|---|---|---|---|---|---|---|
| SPY | +0.00 | +0.02 | −0.01 | +0.26 | **+0.69** | +0.22 | +0.01 |
| QQQ | +0.01 | +0.03 | −0.03 | +0.22 | **+0.69** | +0.19 | +0.01 |

Yahoo's option chain is delayed 15 minutes; the share price the signals read is not.
Every paper fill — real autopilot and shadow lab — was priced off where the share was
15 minutes earlier. A signal that follows the last 15 minutes of movement therefore
buys the option cheap and watches the stale quote "catch up" — a gain no real trade gets.
A signal that fades the move gets the mirror-image penalty.

## Finding 2: that delay explains the lab's follow-vs-fade pattern

The trade simulator, priced the way the app priced (15-minute delay), reproduces 825
closed SPY/QQQ shadow trades (reversal exits excluded) — so the model is right:

| | Exit matches real (target/stop/other) | Mean return: simulated vs real | Correlation |
|---|---|---|---|
| With the app's 15-min delay | **90%** | +7.1% vs +7.2% | +0.79 |
| Real-time pricing | 61% | +3.9% vs +7.2% | +0.25 |

**Label check: PASS** (needs ≥ 75% exit match and mean within 5 points).

Re-priced in real time, the same entries look completely different (mean return per
trade; strategies with ≥ 10 replayable trades):

| Strategy | Real (paper) | Simulated, delayed | Simulated, real time |
|---|---|---|---|
| persistent | +27.3% | +32.0% | −2.6% |
| spy_technicals | +27.0% | +20.9% | +3.6% |
| autopilot_point | +19.7% | +34.9% | +3.4% |
| baseline | +18.6% | +22.5% | +2.8% |
| signal_ungated | +11.6% | +8.1% | −16.2% |
| contrarian | −7.4% | −8.7% | +14.9% |
| fade_conviction | −20.3% | −15.8% | +5.8% |
| inv_persistent | −22.4% | −27.0% | +23.2% |
| null_call | −24.9% | −19.6% | +11.1% |

Every follow-the-signal strategy's paper edge disappears in real time; the fade
strategies flip from losing to winning. The fade numbers are small samples and are NOT
evidence that fading works — that is what the 2016→now run tests. What they do show:
**the July–October paper record (including the 9-of-10 autopilot streak and the
"signal adds ~$15/trade" estimate) measured the quote delay, not the signal.**

## Calibration (used by the label)

- 0DTE implied vol at 10:00 ≈ **1.01 × VIX** (SPY, 164 quotes; middle half 0.90–1.14)
  and **1.49 × VIX** (QQQ, 165 quotes; 1.32–1.79), inverted against the share price
  15 minutes before each quote.
- Typical ATM spread at 10:00: **$0.01** on both.

## Results (2016-01 → 2026-10-16, run 2026-10-19)

5,426 ticker-sessions, 5,412 labelled (14 incomplete or half-days skipped); 20 sessions of
history before any signal scores. Excess = the signal's return minus the coin-flip average
of the call and the put, % of premium per trade, one observation per day (SPY and QQQ
averaged). Noise floor for 20 tests: t = 2.45.

### Verdict

**One pass: `gap_behaviour`, follow** — on days that gap at least a normal amount, trade in
the direction the first 30 minutes moved (whether that extends or fills the gap).
**+3.0% of premium per trade over the coin flip, t = 2.93, positive in all four periods.**

The near-misses are the same idea measured differently — "the 09:30–10:00 move continues":
`orb15` (+2.3%, t 2.18), `opening_drive` (+1.4%, t 1.85), `vwap` (+1.3%, t 1.80) and the live
`technicals` benchmark (+1.0%, t 1.40, negative since 2025). Every fade of them loses.
Everything else (gap direction, prior-day levels, QQQ-vs-SPY, early volatility, VIX change)
is noise.

### How much to believe it

- **The excess is robust to the volatility calibration** — the one input fitted on
  2026 data and applied back to 2016. With implied vol scaled ×0.8 / ×1.0 / ×1.2,
  `gap_behaviour` stays PASS (+3.5% / +3.0% / +3.2%, t 3.24 / 2.93 / 3.26); `orb15` passes at
  ×0.8 and ×1.2 and narrowly misses at ×1.0 (t 2.46 / 2.18 / 2.53).
- **The net return is not trustworthy.** Even the blind trade comes out positive since 2022
  in the simulation (coin flip +3.5% in 2022–24 at ×1.0, +2.0% at ×1.2), and real buyers of
  same-day options usually lose. Constant volatility, no smile, and a 1-cent spread with no
  slippage flatter the absolute level; they cancel out of the excess (both sides are priced
  the same way). So: the simulation can say a signal beats the coin flip; it cannot say the
  trade beats its costs. Real-time quotes have to answer that.
- **It is fading.** `gap_behaviour` was +3.9 / +5.0 / +1.5 / **+0.3**% across the four
  periods; `opening_drive` and `vwap` show the same decline. Possibly the rise of same-day
  options trading since 2022 changed how the morning move carries through.
- **It is small.** +3% of a ~$1.50 premium is ~$4.50 a contract — roughly a tenth of the
  ~$32 per trade the 15-minute quote delay was adding to the paper record.

### Next

1. Make the live lab's fills honest before promoting anything — a follow-the-morning-move
   strategy measured on 15-minute-stale fills would look great for the wrong reason (Finding 1).
   Either real-time quotes (IBKR) or fills corrected for the share's move in the last 15 minutes.
2. Then pre-register `gap_behaviour` (follow) as a shadow strategy computed live, with
   `orb15` (follow) alongside as the runner-up, and let them run against `null_coin`.
3. Re-run this test on fresh months only (2026-11 →) as the out-of-sample check — no new
   signals added to this list.

### Full tables

Sessions: {'sessions': 5426, 'incomplete or half-day': 14, 'labelled': 5412}

Calibration: k = {"SPY": 1.0096081135083783, "QQQ": 1.4949343065966956}, half-spread = {"SPY": 0.0050000000000000044, "QQQ": 0.0050000000000000044}

Blind trade (return on premium per trade; level not trustworthy, see above):

| Period | Ticker-sessions | Coin flip | Always call | Always put |
|---|---|---|---|---|
| 2016-2018 | 1502 | -1.9% | -1.6% | -2.2% |
| 2019-2021 | 1508 | +0.2% | -0.3% | +0.7% |
| 2022-2024 | 1504 | +3.5% | +1.4% | +5.7% |
| 2025-now | 898 | +2.4% | +3.0% | +1.8% |

All 20 tests (noise floor t = 2.45); per-period cells are mean excess % (days):

| Signal | Side | Days | Excess/trade | t | 2016-2018 | 2019-2021 | 2022-2024 | 2025-now | Net since 2022 | Win % | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gap_behaviour | follow | 1559 | +3.0% | +2.93 | +3.9 (431) | +5.0 (438) | +1.5 (438) | +0.3 (252) | +4.5% | 49 | **PASS** |
| orb15 | follow | 1587 | +2.3% | +2.18 | +1.2 (438) | +3.1 (435) | +3.5 (428) | +1.2 (286) | +4.8% | 48 | **INCONCLUSIVE** |
| opening_drive | follow | 2691 | +1.4% | +1.85 | +1.5 (733) | +2.0 (756) | +1.2 (753) | +0.4 (449) | +4.0% | 46 | **INCONCLUSIVE** |
| vwap | follow | 2712 | +1.3% | +1.80 | +1.1 (754) | +1.6 (756) | +1.8 (753) | +0.3 (449) | +4.3% | 47 | **INCONCLUSIVE** |
| technicals | follow | 2712 | +1.0% | +1.40 | +1.0 (754) | +1.6 (756) | +1.5 (753) | -0.7 (449) | +3.7% | 46 | **FAIL** |
| prior_levels | fade | 2711 | +0.8% | +1.12 | +1.0 (753) | -1.0 (756) | +1.4 (753) | +2.7 (449) | +4.9% | 46 | **FAIL** |
| rel_strength | follow | 2680 | +0.2% | +0.61 | +0.3 (728) | +1.4 (752) | -1.0 (751) | +0.1 (449) | +2.5% | 45 | **FAIL** |
| drive_by_vol | fade | 2691 | +0.3% | +0.41 | -1.2 (733) | -0.0 (756) | +1.5 (753) | +1.1 (449) | +4.4% | 46 | **FAIL** |
| gap_direction | fade | 2691 | +0.3% | +0.36 | +0.0 (733) | -1.9 (756) | +1.7 (753) | +1.8 (449) | +4.9% | 45 | **FAIL** |
| vix_change | follow | 2701 | +0.2% | +0.30 | -0.6 (751) | +2.1 (753) | +0.1 (749) | -1.2 (448) | +2.7% | 45 | **FAIL** |
| vix_change | fade | 2701 | -0.2% | -0.30 | +0.6 (751) | -2.1 (753) | -0.1 (749) | +1.2 (448) | +3.4% | 45 | **FAIL** |
| gap_direction | follow | 2691 | -0.3% | -0.36 | -0.0 (733) | +1.9 (756) | -1.7 (753) | -1.8 (449) | +1.3% | 45 | **FAIL** |
| drive_by_vol | follow | 2691 | -0.3% | -0.41 | +1.2 (733) | +0.0 (756) | -1.5 (753) | -1.1 (449) | +1.7% | 44 | **FAIL** |
| rel_strength | fade | 2680 | -0.2% | -0.61 | -0.3 (728) | -1.4 (752) | +1.0 (751) | -0.1 (449) | +3.7% | 45 | **FAIL** |
| prior_levels | follow | 2711 | -0.8% | -1.12 | -1.0 (753) | +1.0 (756) | -1.4 (753) | -2.7 (449) | +1.2% | 44 | **FAIL** |
| technicals | fade | 2712 | -1.0% | -1.40 | -1.0 (754) | -1.6 (756) | -1.5 (753) | +0.7 (449) | +2.4% | 44 | **FAIL** |
| vwap | fade | 2712 | -1.3% | -1.80 | -1.1 (754) | -1.6 (756) | -1.8 (753) | -0.3 (449) | +1.8% | 43 | **FAIL** |
| opening_drive | fade | 2691 | -1.4% | -1.85 | -1.5 (733) | -2.0 (756) | -1.2 (753) | -0.4 (449) | +2.2% | 44 | **FAIL** |
| orb15 | fade | 1587 | -2.3% | -2.18 | -1.2 (438) | -3.1 (435) | -3.5 (428) | -1.2 (286) | -0.2% | 42 | **FAIL** |
| gap_behaviour | fade | 1559 | -3.0% | -2.93 | -3.9 (431) | -5.0 (438) | -1.5 (438) | -0.3 (252) | +2.3% | 42 | **FAIL** |

Descriptive only - same tests restricted to |score| >= 0.5:

| Signal | Side | Days | Excess/trade | t | 2016-2018 | 2019-2021 | 2022-2024 | 2025-now | Net since 2022 | Win % | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gap_behaviour | follow | 1010 | +4.0% | +2.92 | +5.0 (278) | +5.8 (278) | +4.1 (280) | -1.0 (174) | +5.4% | 50 | **INCONCLUSIVE** |
| orb15 | follow | 793 | +4.0% | +2.61 | +3.9 (177) | +3.3 (228) | +4.4 (233) | +4.7 (155) | +7.9% | 50 | **PASS** |
| opening_drive | follow | 1726 | +2.5% | +2.44 | +2.1 (474) | +3.1 (487) | +4.3 (479) | -0.7 (286) | +5.6% | 49 | **FAIL** |
| technicals | follow | 528 | +4.0% | +2.01 | +5.4 (90) | +4.5 (143) | +6.1 (183) | -1.1 (112) | +7.7% | 51 | **FAIL** |
| vwap | follow | 622 | +2.1% | +1.15 | +2.4 (117) | +4.2 (175) | +1.9 (203) | -0.8 (127) | +5.4% | 49 | **FAIL** |
| vix_change | fade | 1101 | +0.8% | +0.59 | +4.5 (306) | -5.1 (340) | -0.5 (272) | +7.2 (183) | +5.2% | 46 | **FAIL** |
| drive_by_vol | follow | 1726 | +0.5% | +0.51 | +2.9 (474) | +1.5 (487) | -1.4 (479) | -1.8 (286) | +1.6% | 45 | **FAIL** |
| rel_strength | follow | 819 | +0.2% | +0.27 | +0.5 (249) | +1.5 (237) | -2.0 (219) | +0.7 (114) | +2.5% | 46 | **FAIL** |
| prior_levels | follow | 2201 | +0.2% | +0.21 | -0.4 (609) | +2.0 (636) | -1.0 (592) | -0.1 (364) | +2.7% | 46 | **FAIL** |
| gap_direction | follow | 1559 | +0.1% | +0.08 | -0.4 (431) | +2.7 (438) | -0.9 (438) | -2.0 (252) | +2.1% | 46 | **FAIL** |
| gap_direction | fade | 1559 | -0.1% | -0.08 | +0.4 (431) | -2.7 (438) | +0.9 (438) | +2.0 (252) | +4.7% | 45 | **FAIL** |
| prior_levels | fade | 2201 | -0.2% | -0.21 | +0.4 (609) | -2.0 (636) | +1.0 (592) | +0.1 (364) | +4.0% | 45 | **FAIL** |
| rel_strength | fade | 819 | -0.2% | -0.27 | -0.5 (249) | -1.5 (237) | +2.0 (219) | -0.7 (114) | +4.7% | 44 | **FAIL** |
| drive_by_vol | fade | 1726 | -0.5% | -0.51 | -2.9 (474) | -1.5 (487) | +1.4 (479) | +1.8 (286) | +4.7% | 45 | **FAIL** |
| vix_change | follow | 1101 | -0.8% | -0.59 | -4.5 (306) | +5.1 (340) | +0.5 (272) | -7.2 (183) | -0.1% | 45 | **FAIL** |
| vwap | fade | 622 | -2.1% | -1.15 | -2.4 (117) | -4.2 (175) | -1.9 (203) | +0.8 (127) | +3.7% | 45 | **FAIL** |
| technicals | fade | 528 | -4.0% | -2.01 | -5.4 (90) | -4.5 (143) | -6.1 (183) | +1.1 (112) | +1.0% | 43 | **FAIL** |
| opening_drive | fade | 1726 | -2.5% | -2.44 | -2.1 (474) | -3.1 (487) | -4.3 (479) | +0.7 (286) | +0.7% | 42 | **FAIL** |
| orb15 | fade | 793 | -4.0% | -2.61 | -3.9 (177) | -3.3 (228) | -4.4 (233) | -4.7 (155) | -1.2% | 42 | **FAIL** |
| gap_behaviour | fade | 1010 | -4.0% | -2.92 | -5.0 (278) | -5.8 (278) | -4.1 (280) | +1.0 (174) | +1.1% | 41 | **FAIL** |

---

# Intraday + events — 2026-10-20

Does a direction signal help **later in the session**, and do the edges hold or break **by
weekday and on scheduled events**? Spec:
`docs/superpowers/specs/2026-10-20-intraday-events-research-design.md`; tool:
`python research.py intraday` (`analytics/intraday_lab.py`). Same label, data and verdict
rules as above; 32 pre-declared main tests (noise floor t = 2.63).

## Verdict

- **The edge is a morning effect and is gone by midday.** Two passes, both at 10:30:
  - `trail30` follow — trade the 10:00→10:30 move: **+2.1% per trade, t 3.01**, and the most
    stable result so far (+2.3 / +1.8 / +2.4 / +2.0 across the four periods).
  - `orb15` follow at 10:30 — the opening-range breakout: **+2.9%, t 3.19**, stronger since
    2022 (+4.9 / +3.8).
  - 11:30: nothing passes (best t 2.17). 13:00 and 14:00: every test within ±1.
  - All of these — and the 10:00 `gap_behaviour` — are one idea, "the morning move
    continues". They win and lose on the same days; one confirmation, not several.
- **No weekday or event breaks any edge.** Slices (13 per edge, noise floor t = 2.26) for
  `gap_behaviour`@10:00, `trail30`@10:30 and `orb15`@10:30: every slice holds or is
  "weaker, not significant". Closest: `orb15`@10:30 on Fed days, −6.4% vs +3.1% elsewhere
  (t −1.92, 62 days) — below the bar, so no stand-down rule.
- **The blind trade gets worse through the day** (coin flip −0.8% at 10:30 → −6.0% at 13:00;
  descriptive only). Trading the afternoon would add cost, not edge.
- **Ignore** the blind trade's +25.9% for a 14:00 entry on Fed days: the simulation prices
  options off the morning VIX, while real 0DTE options are far dearer just before a 2 pm Fed
  statement. It is the case the spec flagged as untrustworthy.

## Event calendar

Compiled 2026-10-20 into `<data dir>/history/events_raw/` from primary sources, every row
with its source URL: FOMC decisions (federalreserve.gov, 90 incl. the three 2020 emergency
actions; the Sunday 2020-03-15 cut tags Monday), CPI and jobs releases (bls.gov archives,
131 each; October 2025 not published during the shutdown), mega-cap earnings for NVDA,
AAPL, MSFT, AMZN, GOOGL, META (company IR/newsroom archives, 262, all after the close,
identical to Yahoo's dates). Spot-checked against 31 independently known dates: all present.
Options expiry, quad witching and month/quarter ends are computed.

## Next (done in the same change)

`morning_trail_1030` and `morning_orb_1030` join `morning_gap` / `morning_orb` in the lab as
pre-registered strategies (QQQ/SPY, autopilot exits, entered 10:31–10:40), computed live by
`signals/morning.py` from Yahoo 15-minute bars — matching the research's values on
Oct 12–16 to within ~0.01.

## Full tables

Event coverage: fomc 2016-01-27..2026-12-09, cpi 2016-01-20..2026-12-10, jobs 2016-01-08..2026-12-04, after_megacap_earnings 2016-02-17..2026-10-29; tagged days: fomc 88, cpi 129, jobs 129, opex 130, quad_witching 43, month_end 130, quarter_end 43, after_megacap_earnings 190

### Main tests: 32 (noise floor t = 2.63)

| Time | Signal | Side | Days | Excess/trade | t | 2016-2018 | 2019-2021 | 2022-2024 | 2025-now | Net since 2022 | Win % | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10:30 | orb15 | follow | 2038 | +2.9% | +3.19 | +0.4 (548) | +2.6 (588) | +4.9 (561) | +3.8 (341) | +5.5% | 47 | **PASS** |
| 10:30 | trail30 | follow | 2689 | +2.1% | +3.01 | +2.3 (731) | +1.8 (756) | +2.4 (753) | +2.0 (449) | +3.3% | 46 | **PASS** |
| 10:30 | session_move | follow | 2691 | +1.7% | +2.45 | +0.3 (733) | +0.6 (756) | +3.5 (753) | +2.9 (449) | +4.4% | 46 | **INCONCLUSIVE** |
| 10:30 | vwap | follow | 2712 | +1.7% | +2.39 | +1.5 (754) | +0.8 (756) | +2.6 (753) | +2.0 (449) | +3.5% | 46 | **INCONCLUSIVE** |
| 11:30 | session_move | follow | 2692 | +1.5% | +2.17 | -0.1 (734) | +1.9 (756) | +1.7 (753) | +3.1 (449) | +0.2% | 42 | **FAIL** |
| 11:30 | vwap | follow | 2712 | +1.3% | +1.88 | +1.0 (754) | +1.8 (756) | +2.2 (753) | -0.6 (449) | -0.8% | 41 | **FAIL** |
| 11:30 | orb15 | follow | 2221 | +1.5% | +1.87 | -0.2 (616) | +1.8 (606) | +2.0 (620) | +2.9 (379) | +0.2% | 42 | **FAIL** |
| 11:30 | trail30 | follow | 2691 | +0.9% | +1.33 | +0.2 (733) | +1.6 (756) | +1.5 (753) | -0.1 (449) | -1.1% | 41 | **FAIL** |
| 13:00 | trail30 | follow | 2691 | +0.7% | +0.98 | +1.4 (733) | -0.4 (756) | +1.5 (753) | +0.0 (449) | -1.9% | 37 | **FAIL** |
| 13:00 | vwap | follow | 2712 | +0.6% | +0.82 | +1.5 (754) | +0.2 (756) | -0.7 (753) | +1.7 (449) | -2.6% | 37 | **FAIL** |
| 13:00 | orb15 | follow | 2337 | +0.5% | +0.59 | +1.2 (639) | +0.9 (648) | -0.8 (661) | +0.6 (389) | -3.0% | 38 | **FAIL** |
| 14:00 | vwap | follow | 2712 | +0.3% | +0.44 | +1.0 (754) | -0.2 (756) | +0.9 (753) | -0.6 (449) | -1.7% | 35 | **FAIL** |
| 14:00 | trail30 | fade | 2692 | +0.3% | +0.36 | -0.8 (734) | +1.3 (756) | +0.4 (753) | -0.0 (449) | -1.7% | 35 | **FAIL** |
| 14:00 | session_move | fade | 2691 | +0.2% | +0.28 | +0.0 (733) | +0.9 (756) | +1.8 (753) | -3.3 (449) | -2.1% | 34 | **FAIL** |
| 13:00 | session_move | follow | 2692 | +0.2% | +0.25 | +1.0 (734) | -0.4 (756) | -1.0 (753) | +1.7 (449) | -2.8% | 37 | **FAIL** |
| 14:00 | orb15 | follow | 2355 | +0.2% | +0.22 | +0.6 (653) | -0.9 (658) | +0.1 (655) | +1.5 (389) | -2.2% | 35 | **FAIL** |
| 14:00 | orb15 | fade | 2355 | -0.2% | -0.22 | -0.6 (653) | +0.9 (658) | -0.1 (655) | -1.5 (389) | -3.4% | 34 | **FAIL** |
| 13:00 | session_move | fade | 2692 | -0.2% | -0.25 | -1.0 (734) | +0.4 (756) | +1.0 (753) | -1.7 (449) | -2.8% | 36 | **FAIL** |
| 14:00 | session_move | follow | 2691 | -0.2% | -0.28 | -0.0 (733) | -0.9 (756) | -1.8 (753) | +3.3 (449) | -1.9% | 35 | **FAIL** |
| 14:00 | trail30 | follow | 2692 | -0.3% | -0.36 | +0.8 (734) | -1.3 (756) | -0.4 (753) | +0.0 (449) | -2.2% | 34 | **FAIL** |
| 14:00 | vwap | fade | 2712 | -0.3% | -0.44 | -1.0 (754) | +0.2 (756) | -0.9 (753) | +0.6 (449) | -2.3% | 34 | **FAIL** |
| 13:00 | orb15 | fade | 2337 | -0.5% | -0.59 | -1.2 (639) | -0.9 (648) | +0.8 (661) | -0.6 (389) | -2.5% | 36 | **FAIL** |
| 13:00 | vwap | fade | 2712 | -0.6% | -0.82 | -1.5 (754) | -0.2 (756) | +0.7 (753) | -1.7 (449) | -3.0% | 36 | **FAIL** |
| 13:00 | trail30 | fade | 2691 | -0.7% | -0.98 | -1.4 (733) | +0.4 (756) | -1.5 (753) | -0.0 (449) | -3.8% | 36 | **FAIL** |
| 11:30 | trail30 | fade | 2691 | -0.9% | -1.33 | -0.2 (733) | -1.6 (756) | -1.5 (753) | +0.1 (449) | -2.9% | 39 | **FAIL** |
| 11:30 | orb15 | fade | 2221 | -1.5% | -1.87 | +0.2 (616) | -1.8 (606) | -2.0 (620) | -2.9 (379) | -4.6% | 39 | **FAIL** |
| 11:30 | vwap | fade | 2712 | -1.3% | -1.88 | -1.0 (754) | -1.8 (756) | -2.2 (753) | +0.6 (449) | -3.1% | 39 | **FAIL** |
| 11:30 | session_move | fade | 2692 | -1.5% | -2.17 | +0.1 (734) | -1.9 (756) | -1.7 (753) | -3.1 (449) | -4.2% | 39 | **FAIL** |
| 10:30 | vwap | fade | 2712 | -1.7% | -2.39 | -1.5 (754) | -0.8 (756) | -2.6 (753) | -2.0 (449) | -1.4% | 41 | **FAIL** |
| 10:30 | session_move | fade | 2691 | -1.7% | -2.45 | -0.3 (733) | -0.6 (756) | -3.5 (753) | -2.9 (449) | -2.2% | 41 | **FAIL** |
| 10:30 | trail30 | fade | 2689 | -2.1% | -3.01 | -2.3 (731) | -1.8 (756) | -2.4 (753) | -2.0 (449) | -1.2% | 41 | **FAIL** |
| 10:30 | orb15 | fade | 2038 | -2.9% | -3.19 | -0.4 (548) | -2.6 (588) | -4.9 (561) | -3.8 (341) | -3.5% | 40 | **FAIL** |

### Slices

**gap_behaviour (follow) at 10:00** — slice noise floor t = 2.26

| Slice | Days | Excess in slice | Elsewhere | t (difference) | Verdict |
|---|---|---|---|---|---|
| Mon | 294 | +5.0% | +2.5% | +1.03 | holds |
| Tue | 314 | +3.5% | +2.8% | +0.26 | holds |
| Wed | 298 | +0.8% | +3.5% | -1.05 | holds |
| Thu | 315 | +3.7% | +2.8% | +0.39 | holds |
| Fri | 338 | +1.8% | +3.3% | -0.55 | holds |
| fomc | 38 | +5.0% | +2.9% | +0.35 | holds |
| cpi | 84 | +4.2% | +2.9% | +0.31 | holds |
| jobs | 92 | -0.4% | +3.2% | -0.79 | weaker, not significant |
| opex | 72 | +5.9% | +2.8% | +0.57 | holds |
| quad_witching | 22 | +2.2% | +3.0% | -0.09 | too few days |
| month_end | 70 | +2.7% | +3.0% | -0.07 | holds |
| quarter_end | 23 | +0.6% | +3.0% | -0.31 | too few days |
| after_megacap_earnings | 134 | +8.0% | +2.5% | +1.55 | holds |

**orb15 (follow) at 10:30** — slice noise floor t = 2.26

| Slice | Days | Excess in slice | Elsewhere | t (difference) | Verdict |
|---|---|---|---|---|---|
| Mon | 379 | +4.6% | +2.5% | +0.97 | holds |
| Tue | 430 | +2.7% | +2.9% | -0.11 | holds |
| Wed | 421 | +2.2% | +3.0% | -0.36 | holds |
| Thu | 413 | -0.0% | +3.6% | -1.61 | weaker, not significant |
| Fri | 395 | +5.1% | +2.3% | +1.21 | holds |
| fomc | 62 | -6.4% | +3.1% | -1.92 | weaker, not significant |
| cpi | 99 | +6.8% | +2.7% | +0.99 | holds |
| jobs | 91 | +7.7% | +2.7% | +1.11 | holds |
| opex | 92 | +10.2% | +2.5% | +1.93 | holds |
| quad_witching | 29 | +17.6% | +2.6% | +2.23 | too few days |
| month_end | 91 | -0.6% | +3.0% | -0.78 | weaker, not significant |
| quarter_end | 26 | +6.6% | +2.8% | +0.49 | too few days |
| after_megacap_earnings | 142 | +1.6% | +2.9% | -0.35 | holds |

**trail30 (follow) at 10:30** — slice noise floor t = 2.26

| Slice | Days | Excess in slice | Elsewhere | t (difference) | Verdict |
|---|---|---|---|---|---|
| Mon | 502 | +0.8% | +2.5% | -0.97 | holds |
| Tue | 554 | +3.0% | +1.9% | +0.58 | holds |
| Wed | 551 | +2.7% | +2.0% | +0.37 | holds |
| Thu | 543 | +1.4% | +2.3% | -0.54 | holds |
| Fri | 539 | +2.9% | +2.0% | +0.49 | holds |
| fomc | 87 | +2.5% | +2.1% | +0.11 | holds |
| cpi | 128 | +3.6% | +2.1% | +0.46 | holds |
| jobs | 127 | +6.3% | +1.9% | +1.25 | holds |
| opex | 129 | +2.9% | +2.1% | +0.25 | holds |
| quad_witching | 43 | +9.0% | +2.0% | +1.22 | holds |
| month_end | 129 | +1.0% | +2.2% | -0.36 | holds |
| quarter_end | 43 | +3.4% | +2.1% | +0.26 | holds |
| after_megacap_earnings | 185 | +2.1% | +2.2% | -0.04 | holds |

### Blind trade by slice (descriptive only — net level not trustworthy)

| Slice | 10:30 | 11:30 | 13:00 | 14:00 |
|---|---|---|---|---|
| all | -0.8% (5412) | -3.7% (5412) | -6.0% (5412) | -5.4% (5412) |
| Mon | -2.4% (1011) | -5.5% (1011) | -9.0% (1011) | -8.8% (1011) |
| Tue | -0.0% (1115) | -3.6% (1115) | -6.4% (1115) | -5.9% (1115) |
| Wed | -0.8% (1110) | -4.5% (1110) | -5.8% (1110) | -0.3% (1110) |
| Thu | -0.0% (1092) | -2.3% (1092) | -3.3% (1092) | -5.1% (1092) |
| Fri | -0.7% (1084) | -3.0% (1084) | -5.7% (1084) | -7.1% (1084) |
| fomc | -3.6% (175) | -4.3% (175) | -6.7% (175) | +25.9% (175) |
| cpi | +1.0% (258) | -3.1% (258) | -8.6% (258) | -3.8% (258) |
| jobs | +0.4% (258) | -3.1% (258) | -2.5% (258) | -5.5% (258) |
| opex | -3.3% (260) | -3.8% (260) | -3.6% (260) | -8.0% (260) |
| quad_witching | -2.0% (86) | -1.4% (86) | -0.0% (86) | -7.3% (86) |
| month_end | +1.0% (259) | -3.6% (259) | -6.0% (259) | -6.1% (259) |
| quarter_end | -3.4% (86) | -5.5% (86) | -9.2% (86) | -8.1% (86) |
| after_megacap_earnings | +0.8% (379) | -1.5% (379) | -4.0% (379) | -2.4% (379) |
