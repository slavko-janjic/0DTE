# Signal review — 2026-09-17

An honest audit of whether the composite's signals actually predict direction,
and whether any of them constitute a **tradeable** edge. Short answer: **no
signal has a stable, cost-covering edge.** Details below so we don't re-litigate.

**Data:** 37,625 signal snapshots, 2026-07-05 → 2026-09-16, tickers QQQ/SPY/IWM/TSLA/NVDA.
**Grading horizon:** 30 min unless stated. **Key rule:** significance is judged on
*independent* observations (non-overlapping forward windows), never raw minute count —
overlapping 5-min samples on a 30-min window inflate every t-stat by ~√6.

## 1. Per-signal directional accuracy (pooled, 30 min)

50% = coin flip. `indep` is the honest sample size; SE ≈ 0.5/√indep.

| Signal | Accuracy | Indep obs | ~SE | Read |
|---|---|---|---|---|
| order_flow | 50.9% | 243 | ±3.2% | noise |
| technicals | 50.6% | 1,784 | ±1.2% | flat, biggest honest sample |
| volatility_regime | 50.6% | 190 | ±3.6% | noise |
| sentiment | 50.0% | 1,226 | ±1.4% | dead-flat coin flip |
| greeks_iv | 48.9% | 1,910 | ±1.1% | worst; most data; reliably ~50 |

Not one signal is more than ~1 SE from 50%. The two with enough data to trust
(technicals, greeks_iv) hug 50 hardest.

(`prediction_markets` 49.2% and `trump_news` 52.7% also appear in historical rows
but were dropped 2026-09-15 — dead inputs, no longer written.)

## 2. Shadow-strategy lab (real P&L with fills)

- **spy_technicals** — 23 trades, 74% win, +$553, PF 2.69, **t=+2.17, EDGE(+)**. The
  one positive standout; corroborated the SPY-technicals directional cell.
- **baseline** (the blind composite) — 48 trades, 48% win, **−$603**, PF 0.77, no edge.
- **gamma_gate_only** — 28 trades, 29% win, −$1,302, PF 0.25, **t=−2.29, EDGE(−)**.
- Everything else (confluence, nvda_technicals, contrarian, runner, persistent, …):
  |t| < 2, mostly negative, big sample needs.

Caveat flagged at the time: ~11 strategies × |t|>2 ≈ 0.5 false positives expected by
chance, and spy_technicals sat right at the boundary on only 23 trades.

## 3. Walk-forward on spy_technicals (70% train / 30% test, chronological)

Rule space: `technicals` signal, threshold grid × {momentum +1, contrarian −1}.
Noise floor for 12 rules = t 2.23.

- Best-by-total rule (**thr 0.05, momentum**): **REJECTED** — train t=+0.90, test t=+0.82,
  both far below the noise floor. The +43%/+21% totals were accumulation over
  thousands of overlapping trades, not a real per-trade edge.
- Threshold breakdown at 30 min: momentum (sign +1) was positive in **both** splits at
  every threshold, and selectivity helped *out-of-sample* (test t rose +0.82→+1.79 as
  threshold rose 0.05→0.30). Suggestive, but nothing crossed t≥2, and per-trade drift
  was tiny (best +0.034%) — far too small to cover 0DTE spread + theta.

## 4. Horizon sweep (30 / 60 / 120 / 240 min) — the decisive result

Testing whether a longer hold gives technicals enough move-size to matter. It doesn't —
and it exposed *why*. TRAIN vs TEST **disagree in sign**, more sharply at longer horizons:

| Horizon (thr 0.40, momentum) | TRAIN mean (t) | TEST mean (t) |
|---|---|---|
| 30 min | −0.0002% (−0.01) | +0.045% (+1.89) |
| 60 min | −0.025% (−1.04) | +0.077% (+2.37) |
| 120 min | −0.054% (−1.36) | +0.124% (+2.11) |
| 240 min | **−0.184% (−2.25)** | +0.272% (+1.98) |

In the earlier 70% of data, technicals-momentum **lost** money (significantly so at
240 min). In the recent 30%, it made money. A rule fit on history would have chosen the
**opposite** direction. This is a **regime flip / non-stationarity signature**, not an
edge — a real, stable edge makes train and test *agree*. The eye-catching test
t=+2.37 (60 min) is meaningless beside its own train t=−1.04.

## Conclusions

1. **No signal has a stable, tradeable edge.** The composite is coin flips; the one
   hopeful signal (technicals-momentum) is *non-stationary* — its sign depends on the
   period. Even where it's positive, per-trade drift is too small for 0DTE friction.
2. **Don't trade the blind composite** — `baseline` loses money, confirmed independently
   by the shadow lab.
3. The shadow-lab `spy_technicals` EDGE(+) was a small-sample / multiple-comparisons
   artifact; the honest walk-forward reduces it to ~t 0.8–1.8 and then the horizon sweep
   shows the sign isn't stable.

## Recommendations

- **Accept it as a paper / learning lab.** Keep running and measuring; do **not** size
  real money on any current signal.
- **Dump `sentiment` and `greeks_iv`** — flat, and neither anchors a positive rule.
  (Method mirrors the 2026-09-15 removal of prediction_markets/trump_news.)
- **Keep `technicals` as a monitored input, not a trigger.** Orientation is momentum
  (sign +1); any signal lives in the extremes, not near zero.
- **Re-test in ~1 month.** One consistent out-of-sample period is not evidence; two
  consecutive ones would start to be. Re-run sections 3–4 on the next slice.
- **Avoid regime-aware modelling for now** — more parameters on this little data
  manufactures the exact overfit this audit caught.
- `order_flow` and `volatility_regime` remain *inconclusive* (too few independent obs —
  they rarely change); revisit once more data accrues rather than judging them now.
