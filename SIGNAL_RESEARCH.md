# Signal research — 2026-10-19

Does any of ten pre-declared signals improve **the autopilot's trade** (10:00 ET ATM
0DTE option on SPY/QQQ, autopilot exits) over the blind trade? Design:
`docs/superpowers/specs/2026-10-19-signal-research-lab-design.md`. Tools: `research.py`,
`analytics/option_sim.py`, `analytics/session_lab.py`, `data/history.py`.

**Status:** steps 1 (calibration + label check) done. The 2016→now run is waiting on
Alpaca keys for the minute-bar history (`python research.py fetch`, then `run`).

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

## Results (2016 → now)

Pending: `python research.py fetch` needs Alpaca keys in
`C:\Users\slavk\0DTE-data\alpaca_keys.json`.
