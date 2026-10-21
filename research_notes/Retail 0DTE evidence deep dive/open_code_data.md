# Open code, datasets and reproducible backtests for 0DTE index options

Researched 2026-10-20. Scope: what a developer with 2016-2026 SPY/QQQ 1-minute share bars, a Black-Scholes option simulator and (soon) real-time IBKR quotes can replicate or reuse.

Method note: the instruction file `skills/deep-research/references/researcher.md` named in the brief does not exist at the path given, so I followed the brief's own constraints. For Vilkov I downloaded the repo files directly (raw.githubusercontent.com, GitHub API) and read the code and shipped tables, rather than relying on summaries. Local copies are in the scratchpad `...\scratchpad\v\` (Vilkov) and `...\scratchpad\r\` (other READMEs).

---

## 0. Bottom line

1. **The one credible, inspectable academic-grade 0DTE backtest with real quotes is Vilkov's `vilkovgr/0dte-strategies` (SPX/SPXW, 2016-09 to ~2024-05 in the shipped panels). After the August 2026 cost-unit correction, its own README says "no strategy or basket retains a positive net Sharpe ratio."** The shipped, refreshed OOS table has 12 of 14 strategy-protocol rows with negative net Sharpe; the other two are +0.07 and +0.03 (indistinguishable from zero). Gross signal is modest and unstable. This is the best available independent evidence that a 10:00-entry, hold-to-close 0DTE structure with simple 10:00 conditioning does not clear costs on SPX.
2. **Several stale or inconsistent numbers remain in the repo** (details in 1.7). The SSRN abstract and the `docs/` summaries still quote the pre-correction "economically meaningful net performance" results (put ratio net SR 0.93, top-3 basket 0.82). Do not cite those.
3. **Nothing else open is better.** The other GitHub/Kaggle/QuantConnect material is either synthetic-price, mid-fill/zero-cost, tiny sample, non-0DTE, or marketing. The best second source is `Javier-Garzo/ern-wo-options-backtester` (real ThetaData 5-min NBBO, 2017-2026, but short-put-selling replications, not entry-timing research, and a 3-month-old repo with 8 stars). Its 0DTE-only naked-put result is 0.13% annualized.
4. **There is no open, quantified study of "Black-Scholes with VIX-derived IV vs real 0DTE quotes."** The closest evidence is indirect: Albers & Kestner on VIX1D construction bias, Song on pre-FOMC IV accumulation then collapse, Chilingarian on Greeks errors from wrong short-end vol (median 47% gamma error at 0-2 DTE), plus two open repos that report absurd synthetic Sharpe (8.19 iron condor on 38 sessions) and Vilkov's own observation that his results change when real spreads are charged. I give a qualitative picture, not an error bound. The only way to get a number for SPY/QQQ is to measure it yourself against real quotes (suggested test in section 5).
5. **Cheap real data exists for the validation you need** (section 3): Massive/Polygon Options Advanced at $199/month (5+ years, quotes, 1-minute aggregates), ThetaData Value $40/month (1-minute, from 2020-01-01 on the docs table; the pricing page says 6 years), Alpaca (free indicative feed, history only since February 2024), Databento OPRA CBBO-1m (back to 2013-04-01, pay per GB, $125 credit). Kaggle has only 16:00 EOD snapshots for SPY/QQQ.

---

## 1. Close reading: vilkovgr/0dte-strategies

**Identity.** Repo "AI-Augmented Replication Package: 0DTE Trading Rules", author Grigory Vilkov (Frankfurt School; co-author of "0DTEs: Trading, Gamma Risk and Volatility Propagation", Dim-Eraker-Vilkov). Paper: *0DTE Trading Rules: Tail Risk, Implementation, and Tactical Timing*, SSRN 4641356. Created 2026-04-09, last push 2026-08-26, 61 stars, MIT licence, not a product (no marketing; the "gex.live" credit is only the bug reporter). Open issues #2 (PR hardening Massive pagination), #3, #4.

### 1.1 Data and sample
- **Source**: Cboe **30-minute** SPXW option bars (NBBO quotes and sizes, OHLC, volume, underlying), plus ThetaData 1-minute SPX/VIX bars for realized moments. Sample stated as Sep 2016 to Jan 2026 (SSRN abstract now says "to February 2, 2026").
- **Raw Cboe files are not redistributed.** What ships (Git LFS, ~400 MB): `data_opt.parquet` (~3.5M rows, moneyness 0.98-1.02 step 0.001, Akima-interpolated), `data_structures.parquet` (~700K), `vix.parquet`, `slopes.parquet`, `future_moments_{SPX,VIX}.parquet`, `ALL_eod.csv`. **The LFS budget is exceeded so the panels currently cannot be fetched** (README banner).
- **Panels actually stop at 2024-05-01.** Issue #3 (carlosagil, 2026-08-04) shows `data_opt.parquet` and `vix.parquet` end 2024-05-01 while the other files run to Feb 2026; only one commit ever touched `data/`. Consequence: the OOS strategy tables have N = 681-682 days (OOS starts April 2019 after a 252-day burn-in), not the ~2,400 days the paper's sample implies. `tests/test_replication.py` byte-compares only some tables, so CI did not catch this (issue #3).
- Weekday expirations: Mon/Wed/Fri from Aug 2016; Tuesday and Thursday added 2022-04-18 and 2022-05-11 (paper text) so **daily 0DTE only exists for roughly the last 2 of the ~7.6 shipped years**. (SSRN abstract for another paper, O'Donovan, dates the Tue/Thu addition at 2022-05-16; minor disagreement.)
- Tier 2 rebuild: `code/ingest/{massive,thetadata}/download_spxw.py` (needs a subscription).
- Interpolation: Akima across moneyness only (not across time); the paper's own limitation list says "strategy construction uses interpolated data; results may differ with actual exchange quotes".

### 1.2 Strategies tested, and entry/exit times
Seven templates (`docs/agent-context/strategies.md`, `compute_implementable_pnl.py::get_legs`), all defined by moneyness M = K/S:
| Template | Legs |
|---|---|
| Strangle/Straddle | long P(low) + long C(high) |
| Iron Butterfly/Condor | long P(l), short P(ml), short C(mh), long C(h) |
| Risk Reversal | short P(low) + long C(high) |
| Bull Call Spread | long C(l), short C(h) |
| Bear Put Spread | long P(h), short P(l) |
| Call Ratio Spread | long 1 C(l), short 2 C(h) |
| Put Ratio Spread | short 2 P(l), long 1 P(h) |

Representative near-ATM configurations used for the conditional work (`output/tables/0dte_conditional_representative_moneyness.tex`), all with max |M-1| = 0.005 and 952-953 days: bear put 0.995/1; bull call 1/1.005; call ratio 1/1.005; iron fly/condor 0.995/0.997/1.003/1.005; put ratio 0.995/1; risk reversal 0.995/1.005; strangle 0.995/1.005. (The `.csv` in `data/` still has max_moneyness_dev 0.01, i.e. stale vs the table.)

- **Main entry 10:00 ET, hold to 16:00 ET cash settlement** (no intraday management, no hedging; "held-to-close positions only"). Appendix entries: 13:00, 15:00, and 16:00 the prior day (1DTE at entry, 0DTE next morning). Mid price = benchmark.
- Everything is **static buy-and-hold to settlement**, so there is no exit spread and no stop/take-profit logic.

### 1.3 Conditioning variables used, and what is NOT used
Features (`compute_conditional_oos_protocol.py::build_feature_frame` and `feature_cols`, read from code): ten features, standardized within the training window, **logistic regression, L2 C = 1.0**:
1. `iv`: 10:00-to-16:00 integrated implied variance from the 0DTE SPXW chain (VIX methodology, x1e5)
2. `isk`: implied skewness = IV_up minus IV_dn
3. `slope_up`, `slope_dn`: vol-surface slopes (point-in-time at 10:00)
4. `SPX_lret`, `SPX_lrv`, `SPX_lrv_skew`: **previous observation's** return, realized variance, realized skew (shift(1) on the merged frame; with Mon/Wed/Fri expiries early in the sample "lagged" can be more than one calendar day old)
5. `pnl_l1`, `pnl_mean5_l1`, `pnl_std5_l1`: lagged strategy net PNL, 5-day mean, 5-day SD

Target `y = 1{pnl_net > 0}`; position w = sign(p-0.5) (hard) or 2p-1 (soft); realized return = w x net PNL (directional, so short side allowed). Windows: expanding and rolling 252d.

Also analysed but not used as OOS features: realized variance/skewness drivers (Table 4/regressions), liquidity (half-spread, depth, relative spread), flow (volume, delta/gamma/vega notional), GEX-style proxies (signed/absolute OI-weighted convexity), ratios B^Gamma, R^Gamma, T. Unconditional VIX-regime sort (terciles of 10:00 implied variance), Table 6 (see 1.6).

**Not used anywhere**: overnight gap, 09:30-10:00 morning move, day of week, calendar/event dummies (FOMC, CPI, jobs), VIX level vs VIX1D, intraday IV change since open. **Future work as stated in the paper** (section 7 / SSRN conclusion): "keep updating data under locked out-of-sample governance", "extend event conditioning", monitor whether better strategy families stay stable. Stated scope limits: hold-to-close only, no dynamic intraday hedging, no causal market-impact design, conditioning restricted to information available by 10:00 ET.

### 1.4 Cost model (and the August 2026 correction)
From `KNOWN-ISSUES.md`, `compute_implementable_pnl.py`, `docs/agent-context/method.md`:
- Three execution tiers: **mid**; **mid minus half the observed bid-ask spread per leg** ("B/A"); B/A **plus a flat 0.5 bp slippage-and-fee charge** ("B/A+0.5bp", in underlying-relative PNL units). Positions are entered at 10:00 only and settle in cash, so there is no exit cost.
- **Units**: `bas`, `mid`, `tv` are *fractions* of spot; `reth_und` and `reth` are *percent* of spot (`reth_und = 100*(payoff - mid)`). Until August 2026 the code subtracted the fraction from the percent, so **the half-spread was charged at 1/100 of its true size (about 0.022 bp instead of 2.2 bp)** while the 0.5 bp fee was right. Fixed in commit "Fix transaction-cost unit-scale error in bid-ask half-spread" (2026-08-26); a regression guard `cost_units.py::assert_percent_of_spot_scale()` was added. Reported by the gex.live team, who reproduced the old table to four decimals before diagnosing it. Independent sanity check in KNOWN-ISSUES: `bas*spot` implies a mean dollar spread of about **$1.33** for SPXW (if `bas` were percent it would be $0.013, below the tick).
- Mean half-spread after fix: **about 2.2 bp** (of spot); entry turnover for a two-leg strangle rises from an apparent 0.24 bp to **24 bp**.
- Earlier bug (issue #1, Victor Yoong, fixed 2026-05-14): directional net PNL computed as `sign * pnl_net` flipped the cost sign on short days (costs added instead of subtracted). Fix: `sign * gross - cost`, and gross columns were added to all tables.
- **Model-zoo caveat** (KNOWN-ISSUES): `compute_conditional_model_zoo.py`, `derive_binary_decision_summary.py` and the anchor-zoo path use a flat `--net-cost 0.005` (0.5 bp) that **excludes the half-spread**; the author says those net figures "understate realistic cost by roughly 5x and should be read as an upper bound."
- Note on target contamination: because `y = 1{pnl_net > 0}` depends on cost, "anyone rescaling the cost only at the evaluation step, without also rebuilding the target, will get a more favorable number than the one reported here."

Cost per structure implied by the shipped corrected table (Mean Mid minus Mean B/A, % of underlying; my arithmetic): strangle 0.0106% (1.06 bp), iron fly/condor 2.51 bp, risk reversal 0.83 bp, bull call 3.17 bp, call ratio 3.54 bp, bear put 2.97 bp, put ratio 3.43 bp; add 0.5 bp flat. So **round-trip-free entry costs are 1.3 to 4.0 bp of spot per structure on SPX at 10:00**, comparable to or larger than the gross edge.

### 1.5 Results after the correction (shipped, refreshed tables)

**Unconditional, 10:00 entry, hold to close, 952-953 days** (`output/tables/0dte_implementable_pnl.tex`; Mean columns in % of underlying; SR annualized sqrt(252)):
| Strategy | Mean mid | Mean B/A | Mean B/A+0.5bp | SR mid | SR net | Turnover (bp) | ES 1% (%) |
|---|---|---|---|---|---|---|---|
| Strangle/Straddle | -0.0059 | -0.0165 | -0.0215 | -0.27 | **-0.97** | 24.1 | 1.609 |
| Iron Butterfly/Condor | -0.0073 | -0.0324 | -0.0374 | -0.56 | **-2.67** | 31.0 | 0.787 |
| Risk Reversal | 0.0157 | 0.0074 | 0.0024 | 0.65 | **+0.10** | 2.7 | 1.645 |
| Bull Call Spread | -0.0101 | -0.0418 | -0.0468 | -0.35 | **-1.59** | 79.1 | 1.067 |
| Call Ratio Spread | -0.0140 | -0.0494 | -0.0544 | -0.55 | **-2.11** | 74.9 | 1.327 |
| Bear Put Spread | 0.0138 | -0.0159 | -0.0209 | 0.48 | **-0.73** | 62.9 | 0.922 |
| Put Ratio Spread | 0.0251 | -0.0092 | -0.0142 | 1.06 | **-0.61** | 57.4 | 0.768 |

Before/after per KNOWN-ISSUES (net SR): put ratio +0.84 to -0.61; risk reversal +0.44 to +0.10; bear put +0.30 to -0.73; strangle -0.51 to -0.97; iron fly/condor -0.96 to -2.67. "No structure retains a materially positive net Sharpe ratio." **Lesson: mid-price Sharpes of +0.5 to +1.06 vanish at the touch.** The risk reversal's mean net of +0.0024% of spot (0.24 bp) with loss probability 52.7% and ES1% 1.64% of spot is not an edge.

Tail risk (same cost basis, `0dte_tail_risk_diagnostics.tex`): worst day -0.96% (put ratio) to -2.84% (risk reversal); strangle worst 5-day -7.77%; max drawdown of cumulative daily net PNL 7.9 (risk reversal) to 52.1 (call ratio) in % of spot units; loss probabilities 48.7% (iron fly) to 69.9% (strangle).

**Conditional OOS, logistic benchmark, N = 681-682 days** (`output/tables/0dte_conditional_oos.tex` and `data/conditional_oos_protocol_summary.csv`, refreshed post-fix; bp of underlying):
| Strategy | Protocol | Hit % | Mean gross | SR gross | Mean net | **SR net** | Calib. slope |
|---|---|---|---|---|---|---|---|
| Strangle/Straddle | Expanding | 68.5 | 1.014 | 0.33 | -0.889 | -0.29 | 0.18 |
| Strangle/Straddle | Rolling | 68.5 | 2.126 | 0.69 | 0.222 | **+0.07** | 0.19 |
| Iron Fly/Condor | Expanding | 65.4 | 0.353 | 0.65 | -3.162 | -4.13 | 0.74 |
| Iron Fly/Condor | Rolling | 65.8 | 0.589 | 1.09 | -2.925 | -3.80 | 0.58 |
| Risk Reversal | Expanding | 67.9 | 2.012 | 0.48 | 0.108 | **+0.03** | 0.30 |
| Risk Reversal | Rolling | 68.2 | -0.447 | -0.11 | -2.350 | -0.55 | 0.47 |
| Bull Call Spread | Expanding | 60.1 | 0.736 | 0.56 | -1.391 | -1.04 | 0.32 |
| Bull Call Spread | Rolling | 58.1 | 0.502 | 0.38 | -1.625 | -1.22 | 0.13 |
| Call Ratio Spread | Expanding | 59.2 | -1.410 | -0.60 | -4.218 | -1.69 | 0.17 |
| Call Ratio Spread | Rolling | 59.5 | -1.515 | -0.64 | -4.322 | -1.73 | 0.23 |
| Bear Put Spread | Expanding | 64.6 | 0.100 | 0.07 | -2.137 | -1.52 | 0.10 |
| Bear Put Spread | Rolling | 63.4 | 0.036 | 0.03 | -2.201 | -1.57 | 0.10 |
| Put Ratio Spread | Expanding | 65.1 | 1.556 | 0.77 | -1.405 | -0.70 | 1.01 |
| Put Ratio Spread | Rolling | 65.2 | 1.862 | **0.93** | -1.100 | -0.55 | 0.70 |

Observations from the table: (a) the best gross SR is 1.09 (iron fly rolling) yet its net is -3.80 because the 4-leg structure pays about 3.5 bp per day; (b) the gross edge for the best series (put ratio, gross ~1.6 to 1.9 bp/day) is about the size of the cost (about 3 bp), so net is negative; (c) calibration slopes mostly 0.1-0.3 (far below 1: forecast probabilities are badly over-dispersed; only put ratio expanding reaches 1.01); (d) expanding vs rolling gaps flip sign across strategies (risk reversal expanding +0.03 vs rolling -0.55), a sign of noise rather than structure; (e) hit rates 58-69% mostly reflect the base rate of `y` (the unconditional loss probabilities in the tail table are 49-70%, so a trivial predictor would score similar), do not read them as skill. The Brier scores (0.211-0.251) are close to the 0.25 coin-flip value.

**Pre-correction headline numbers still quoted in the SSRN abstract, `docs/agent-context/results-summary.md`, `docs/paper/*`, `paper-claims.csv` (C11, C12) are invalid**: put ratio SR gross 1.18 / net 0.93, strangle net 0.39, top-3 basket gross 1.12 / net 0.82, all-strategies 0.81 / 0.25. KNOWN-ISSUES states post-fix on the full sample: put ratio net -0.75 and top-3 basket -0.82 (the sign of the basket flips).

### 1.6 VIX-regime conditioning (unconditional sort, mid prices)
`output/tables/0dte_vix_regime_1000.tex` (mean PNL in % of underlying; terciles of 10:00 implied variance; Obs 939-940). The only significant High-minus-Low is **Put Ratio Spread: Low -0.0065, Mid +0.0115, High +0.0713, H-L +0.0777, t = 2.50, p = 0.013**. All six others have |t| <= 1.40 (risk reversal H-L +0.0436, t 1.20; bear put +0.0452, t 1.19; iron fly -0.0232, t -1.40; strangle -0.0142, t -0.44; bull call -0.0159; call ratio -0.0271). With seven strategies tested, one p = 0.013 is roughly what chance delivers; the paper itself calls the VIX filter "simple but not sufficient for implementation". These are mid-price (gross) numbers: the High-VIX put-ratio mean of 7.1 bp is about two times the 3.4 bp half-spread plus 0.5 bp fee, so even this best case would net only about 3 bp before tail risk, and high-VIX states are where gap and tail risk are largest.

### 1.7 Credibility assessment and open problems
Strengths: real NBBO quote bars, transparent code, parity tests, public bug history with credit, author willing to publish a result-reversing correction (README says "reverses the sign of the net-of-cost conclusions"). Weaknesses to keep in mind:
- **Stale artifacts**: SSRN abstract, `docs/`, `paper-claims.csv`, `data/conditional_oos_protocol_predictions.parquet` and `output/tables/0dte_conditional_oos_investment.tex` are not refreshed. The "investment" table in `output/tables/` still shows pre-fix numbers (top-3 basket SR net 0.82, 1,061 days, put ratio 0.71), inconsistent with KNOWN-ISSUES (-0.82). It is built from the stale predictions parquet and a different day count (1,061 vs 682), so treat it as unreliable (issue #3 also reports a 1,061 vs 682 day mismatch and different Sharpes when regenerated from the panels: put ratio 1.56, top-3 1.10, all 0.66, on the pre-sign-fix costs).
- **Data panels end 2024-05-01** (issue #3), so the corrected conclusions rest on 2016-2024 data of which only about two years has daily expiries.
- **Cost realism cuts both ways.** (i) 30-minute bars give the quote at the bar, not a fill; half-spread per leg is a standard but unverified fill assumption for multi-leg complex orders (complex-order books and price improvement via PIM/AIM could do better; retail marketable orders do worse in stress). (ii) The 2.2 bp mean spread is for SPXW across ±2% moneyness and the 10:00 bar; it is not a SPY or QQQ number.
- **Multiple comparisons**: 7 strategies x 2 protocols x ~10 features, no deflation of Sharpe; the paper's Benjamini-Hochberg correction is only for the moment regressions (Table 4).
- **Open issue #4** (user Screamit99, 2026-08-27) reports an independent rebuild on "non-Cboe sources" (1,856 days, Jun 2017 to Aug 2026, minute mids, nearest strike not Akima): "all seven structures unconditionally approximately zero gross", realized skew vs directional-structure PnL correlation 0.4-0.6 (RV 0.04-0.07), and put ratio OOS rolling gross SR about 0.4-0.75 vs Vilkov's rolling 0.93. It is a self-reported, unverifiable account with no shared code; use as weak corroboration only. It also asks whether rolling (not expanding) is the honest central estimate and whether the 10:00 implied block vs lagged-realized block drives the signal (no ablation yet; the author had not replied as of the data I retrieved).
- Entry grid is 30-minute bars: the code cannot test arbitrary entry minutes.

### 1.8 What is directly reusable for your project
- **Cost evidence for planning**: charge half the spread on every leg plus about 0.5 bp of spot; on SPX that was 1.3-4.0 bp of spot per structure. For SPY/QQQ you must measure it from your own IBKR quotes: per-share cost = bp x spot (3 bp of $600 = $0.18/share, i.e. 18 cents on a spread vs a $0.01 SPY tick), which shows why only real quotes can decide it.
- **Protocol template** (`compute_conditional_oos_protocol.py`): expanding/rolling 252d logistic classifier on a 0/1 net-profit target, hard sign mapping, features standardized in-window, gross and net reported separately, calibration slope and Brier reported. This is a clean template; the lesson from the correction is to rebuild the target when the cost model changes.
- **Moment features** (IV, implied semivariances/skew via VIX methodology, realized semivariances from 1-minute bars) are straightforward to reproduce from SPY/QQQ 1-minute bars (realized) plus real chains (implied).
- **Sanity targets for your own simulator**: unconditional mid-price means are near zero, mid-price SRs of +0.5 to +1 for put ratio/risk reversal disappear with spreads. If your Black-Scholes simulator shows a stable net edge for these structures, suspect the simulator.

---

## 2. Other open repositories and notebooks (0DTE backtests)

GitHub search terms used: "0dte", "zero-dte", "spx 0dte", "0dte options", "0dte iron condor", "spxw backtest", "0dte quantconnect", "thetadata", "0dte backtest in:readme" (the CLI search returned nothing for several multi-word phrases; the keyword listing below is therefore not exhaustive). Kaggle was queried through its public dataset list API (no 0DTE-specific datasets exist). QuantConnect forum reviewed via search.

| Repo | Data | Period | Strategy | Cost assumption | Result claimed | Credibility |
|---|---|---|---|---|---|---|
| **Javier-Garzo/ern-wo-options-backtester** (8 stars, Apache-2.0, created 2026-07-26) | ThetaData **5-minute NBBO + Greeks + IV** (SPXW, SPY, XSP), Cboe VIX history; ~36 GiB SQLite | 2017-01-03 to 2026-07-31 | Replications of Early Retirement Now (ERN) naked 0DTE/1DTE put selling and WealthyOption (WO, 1-4 DTE, no 0DTE entries) | Fills at 60% (puts) / 65% (calls) of spread; five-minute data so GTC profit-target fill inferred; fees; no margin calls/liquidation | ERN: **0DTE-only naked puts 0.13% annualized, max DD 1.19%**; 1DTE-only 1.30%; 0DTE+1DTE 1.55% annualized, 1.81% max DD, 3,198 trades, 2.50% losing trades (vs ERN live 7.06% CAGR, 2.30% vol, IR 3.08, Jan 2018-Sep 2025). WO SPXW three puts 13.97% CAGR / 22.90% max DD vs official 19.0% / 21.2%; puts+calls 17.58% / 21.43% vs 23.1% / 19.2% | **Highest of the non-academic repos**: real quotes, postmortems that admit local < official, results JSON committed. Caveats: new, tiny community, 5-min sampling, short-put premium selling not directional timing, requires a ThetaData subscription to rerun, WO annual tables show -0.87% (2022) and -0.24% (2024) years and 22.8% drawdowns. Directly relevant data point: standalone 0DTE puts "highly sensitive to gaps and five-minute stop execution" |
| **M-man2591/0dte-research-platform** (MIT) | **Synthetic Black-Scholes chain** with per-session IV from lagged daily VIX; SPY from yfinance | SPY daily 2023-08 to 2026-08; options engine only 38 sessions | MA cross, RSI, ML, PPO; put credit spread, iron condor | 1 bp costs; bid-ask in options engine | Equity strategies all lose to buy-and-hold (Sharpe 1.37). Synthetic put spread Sharpe 1.79, **iron condor Sharpe 8.19 with 92% win rate on 38 calm sessions, explicitly "simulator artifacts by construction"** | Unusually honest: README and CODE_REVIEW.md list that a Sharpe of 36.5 was a bar-count annualization bug (really 1.79), a look-ahead leak (`lunch_lull`), hardcoded IV ignoring VIX. Useful as a **negative example for your simulator**, and its LEARNINGS.md notes the exact problems (no smile, no jumps, no 14:00 FOMC, no spreads). Not evidence of any edge |
| **thunderscarf/SPX_0DTE_Options_Selling_Public** (Sep 2025, MIT) | Polygon.io SPX chains + VIX1D | 537 days (trades.csv 2023-06-01 on) | Sell 5-wide credit spread outside VIX1D-implied half-day expected move at 09:45, trend filter (ADX/MAs) | **Fills at mid; no commissions, no slippage in code** (`main.py` lines ~99-105: `(bid+ask)/2`) | Win rate 78.17%, EV $25.73/trade, 15.74% annualized, **Sharpe 0.97, max DD -16.21%** | Low. Code is inspectable but mid fills inflate everything (compare Vilkov: mid SR 0.5-1.06 becomes negative); 1 contract, $10k start, one regime (2023-2025). Promotes a Substack |
| **emlama/gex-backtesting** | Polygon flat files: **513 days of SPX 0DTE option trades** (2024-01-02 to 2026-02-19), ~400K-900K trades/day, quote-matched trade side; 2.3 GB | 2024-2026 | GEX/"GCI" concentration indices vs late-day PUT price explosions; pre-registered tests, Fisher exact, FDR | n/a (hypothesis tests, not P&L) | Notebooks `01_gci_spike_analysis`, `02_multi_metric_backtest`. I did not rerun them | Medium-low: sound statistical hygiene (pre-registration, FDR) but trade-level data and custom metrics; the GEX proxy needs dealer-sign assumptions; 15 stars |
| **FlashAlpha-lab/flashalpha-historical-examples**, `0dte-options-analytics`, `flashalpha-quantconnect` | FlashAlpha Historical API (SPY minute chain since 2018-04-16, GEX/VRP/vanna) | 2017/2018 on | "21 backtest essays" on LEAN | n/a (pre-launch) | none yet | **Marketing for a paid product**: "Historical API requires the Alpha tier ($1,499/mo)". Treat all as sales material |
| **jefrnc/ibkr-odte-strategies** (17 stars) | none (live IBKR; `backtest` CLI stub; yfinance) | n/a | ODTE "breakout" on **single stocks** + earnings straddle; $5k account | n/a | none | Not index 0DTE, no results, sponsor-ware ("Support this project"). Skip |
| **brigoraoul/spy0dte-simulator** | User supplies **OHLC CSV** (recommends Polygon); prices options itself | n/a | Bull put / bear call credit spreads, MLflow | not documented | none | Framework only, no results; same synthetic-price weakness as yours |
| **askosyrskiy/iron_fly** and `iron_fly_strategy` (Oct 2026) | Own tape: SPX 1-second prints and option chain every 2 seconds, gzipped on Cloudflare R2 (~1.5 MB/day) | recent days only (default hydrate last 10) | "Dale's" iron fly add/drop rules (YouTube-inspired) | replay only | none published | A replay tool, no performance claim, recent window; the **2-second chain tape format is the interesting idea** for your IBKR recording. |
| aicheung/0dte-trader (97 stars, 2023), 1797346220/qqq-trading-system, SebastianEgebjerg/DeepHedging0DTEOptions, sparrow-capital/nautilus-zerodte, khakhasshi/OptionWorkstation, Elias-outofsample/spx-options-pipeline | various | | live bots, deep hedging (data proprietary, BS-synthetic sample), ThetaData-based replay/data lakes | | no verifiable results | Plumbing, not evidence. `spx-options-pipeline` (ThetaData 1-minute Greeks for 8 expiries since 2019, point-in-time loader, README lists vendor feed defects: OPRA test contracts with expiry 1882, 09:30 bar with underlying = 0 and IV overflow, zero-bid rows) is a useful **data-cleaning checklist**; licence is "all rights reserved" though |

### Academic/vendor "backtests" you can read but not rerun
- **Perz, "Profitability of selected 0DTE index options strategies"** (Communications of the IBIMA 2024; SSRN 7162898): iron condor on SPX 0DTE using **one-minute data over the past 12 months**; "both variants proved profitable"; documents implied volatility overstated vs realized. Small sample, single year, costs not stated in the abstract. Code not found.
- **Kam, "The Efficacy of 0DTE Options Strategies: An Empirical Study of 3,909 Option Alpha Backtests"** (SSRN 7055179): 90.6% SPX; 78.7% iron condors/butterflies/credit spreads; median win rate 53.7%, median profit factor 1.98, **99.9% of backtests profitable**, profit factors up to 138.06, many small samples; the author concludes reported "top" performance "materially overstates realistic forward expectations". It is a selection-bias study of user-submitted vendor (Option Alpha) backtests, not an edge estimate.
- **GreeksLab "Trading 0DTE SPX on Fed Day"** (blog by a paid backtesting vendor, OptionMighty LTD): 32 FOMC days Jun 2022-Apr 2026 vs ~970 normal days; short ATM straddle 09:35: win 69%, avg +$908/day, worst -$3,947; short 20-delta strangle at 13:55: win 81%, +$286, worst -$1,660; normal-day short ATM straddle at the open averaged -$51/day (-$49,737 total). **Explicitly zero commissions and zero slippage** ("upper bound"); data source not stated; OTM premium total fell 27.9% in the 15 minutes after 14:00 on Fed days vs 20.8% on normal days. Marketing, but the IV-crush magnitude is a usable order-of-magnitude fact.
- **QuantConnect forum**: threads show the recurring problems: stop fill 7.8 vs 5.9 stop price (1.9 slippage) on a 0DTE iron condor; default backtests fill at mid; users hand-write fill models (mid-price fill, fixed $0.10 slippage); inconsistent SPX index option prices depending on resolution; data gaps on some days (forum 19084). I did not find a QuantConnect community algorithm with audited 0DTE performance numbers. QuantConnect/Lean data: AlgoSeek (from OPRA) US equity and index options from January 2012, minute/hour/daily; SPX and SPXW covered; page does not confirm SPY/QQQ listing or 0DTE explicitly.

**Kaggle**: no 0DTE or intraday SPY/QQQ option-quote datasets found. Available are EOD only: `kylegraupe/spy-daily-eod-options-quotes-2020-2022` (CC0, 271 MB, quotes at 16:00), `kylegraupe/qqq-daily-option-chains-q1-2020-to-q4-2022` (CC0, 132 MB), `dudesurfin/spy-options-eod-volatility-surface-2010-2023` (MIT, 509 MB parquet, 16:00 snapshot, includes DTE), `shankerabhigyan/s-and-p500-options-spy-implied-volatility-2019-24` (CC0, 1.09 GB, "2014-2025", bid/ask/sizes/IV/Greeks, daily), `jasonli88/spyoptionstradetickerdata` (a few months of trade ticks). At 16:00 the next-day expiries are 1DTE, so they can only test "16:00 previous day" 1DTE entries, and cannot test 0DTE entry at 10:00.

---

## 3. Datasets of historical intraday SPY/QQQ/SPX option quotes

Prices and tiers were read from vendor pages on 2026-10-20 unless noted; verify before buying. "1m" = 1-minute.

| Source | Resolution / content | History | Cost (as read) | Notes |
|---|---|---|---|---|
| **Massive (formerly Polygon.io) Options** (massive.com/pricing) | minute aggregates all plans; trades (Developer+), **quotes (Advanced only)**; Greeks/IV real-time (Starter+); flat files | Basic $0: 2 years, EOD, 5 calls/min; Starter $29: 2 years, 15-min delayed; Developer $79: 4 years; **Advanced $199: 5+ years, real-time, trades and quotes** | $0 / $29 / $79 / $199 per month | Cheapest route to historical quotes for SPY/QQQ/SPXW; quotes need Advanced. Vilkov's repo has a Massive ingest adapter (`code/ingest/massive`). History depth for SPY 0DTE specifically not tested |
| **ThetaData** (thetadata.net/pricing, docs "Subscriptions") | Options: FREE = EOD, from 2023-06-01 (1 year, 30 req/min; text says 20); VALUE = **1-minute** from 2020-01-01; STANDARD = **tick-level** from 2016-01-01; PRO = tick from 2012-06-01 | pricing page: Value 6 years, Standard 10, Pro 14 | **Value $40, Standard $80, Pro $160 per month** (options). Commercial use quoted at $1,600/month | Best price/performance for NBBO history; used by Vilkov (1-min SPX/VIX), Javier-Garzo (5-min NBBO), Elias pipeline (1-min Greeks, 8 expiries since 2019). Python/Rust SDKs, local Terminal (JVM). Docs table and pricing page disagree on Value depth (2020 vs 6 years); stock history for CTA-tape names (SPY) is limited to 2020-01-01 |
| **Databento OPRA.PILLAR** | schemas include **CBBO-1m** (NBBO + last sale 1-minute), trades, OHLCV, definitions; CMBP-1 | CBBO-1m back to **2013-04-01** (backfilled 2025-05-21) | Pay-as-you-go per GB or subscription; Standard plan $199/month; $125 free credit (6 months); exact $/GB for CBBO-1m not on the page, use their cost estimator | Needs symbol filtering (OPRA is terabytes/day at CMBP-1); fit for SPY/QQQ 0DTE sample pulls |
| **Cboe DataShop "Option Quote Intervals"** | 1, 5, 10, 15, 20, 30, 60, 390, 405-min intervals with NBBO sizes, OHLC, volume | **January 2012 to present** | Monthly or annual subscription; price not shown; SPX needs a separate Cboe Global Indices Feed licence "starting at $1000/month"; SPY ~20 GB/year compressed at 1-min with calcs, SPX ~45 GB/year | This is Vilkov's source (30-min). QQQ not named on the page. A sample download link exists |
| **ORATS** | 1-minute full chains, Greeks, IV | Aug 2020 (S3 delivery; hard-drive Oct 2020 onward; separate 2-minute archive 2015-01 to 2020-09); EOD from 2007 (snapshot 14 min before close) | price not on page (orats.com/one-minute-data) | Daily files via S3; about 21 GB per trading day (28 TB 1-min total) |
| **QuantConnect / AlgoSeek** | minute/hour/daily trade+quote bars for US equity and index options | Jan 2012 | via QC data library credits/subscription; not stated | Integrated backtester; community reports data gaps and mid fills by default |
| **Alpaca options** | bars, trades, quotes; free "indicative" feed (derived, not OPRA) vs paid OPRA | **only since February 2024** | free indicative; OPRA paid | Too short for 2016-2026 but free for a recent validation sample; indicative quotes are not real NBBO |
| **OptionsDX** | EOD and intraday snapshots for SPY, SPX, QQQ among 10 products | not stated on the page | product price range shown as $0-$50 (SPY, SPX) and $0-$20 (QQQ) | Page did not list resolution or free samples; verify. Historically used by retail backtesters |
| **HistoricalOptionData.com** | market-wide snapshots every **5 minutes**, bid/ask/last/IV/Greeks, FTP CSV | not verified | not verified | Found via search only |
| **FlashAlpha Historical API** | SPY (not SPX) minute chain, GEX/VRP | 2018-04-16 | Alpha tier $1,499/mo | Vendor marketing; do not use |
| Kaggle (above) | EOD 16:00 only | 2010-2025 | free | Not usable for intraday 0DTE |
| iVolatility | intraday option **trades** with bid/ask at trade time | since March 2014 | not verified | search result only |

Practical reading for you: a one-month ThetaData Value or Massive Advanced subscription ($40 or $199) gives enough real SPY/QQQ 1-minute quotes to calibrate your simulator and your cost model; Databento CBBO-1m is a pay-per-use alternative with the longest history (2013). IBKR itself provides no historical option quotes, so the live recording you plan is the only free source going forward (a recording format like iron_fly's 2-second chain tape is a sensible design).

---

## 4. Evidence on how wrong Black-Scholes / VIX-derived IV is for 0DTE

**Direct answer: I found no open study that prices 0DTE with flat or VIX-derived IV and reports the dollar error against real quotes.** What exists is below, with what each implies for a synthetic simulator. Treat the qualitative conclusion as established and the magnitudes as unmeasured.

| Source | Finding | Implication for a BS/VIX simulator |
|---|---|---|
| **Albers & Kestner, "The daily rise and fall of the VIX1D: causes and solutions of its overnight bias"**, Finance Research Letters 2024 (SSRN 4650419) | VIX1D "consistently rises during trading hours and falls overnight" because of business-time weighting and dynamic weighting of next-term options which carry overnight variance risk premium; stronger than the day-of-week effect. Largest spikes coincide with CPI and FOMC press conferences (per secondary summary) | VIX1D is not a clean, constant "IV of the 0DTE you are trading"; using it as the sigma at 10:00 imports a time-of-day drift. A VIX (30-day) input is a structural proxy only: the 0DTE IV can sit far from it on event days |
| **Song, "Pre-FOMC Uncertainty Accumulation: Evidence from 0DTE Options"** (SSRN 7412318, 2026) | Implied uncertainty "monotonically rises from 10:00 ET to the 14:00 ET release and largely collapses at the announcement"; same pattern around FOMC Minutes and the closing auction; implied skewness uniformly negative and stable | A constant-sigma simulator misprices event days both ways: it understates the premium paid in the hours before 14:00 and misses the post-announcement collapse. If you enter at 10:00 and exit at 16:00 the intraday path matters for any exit before expiry |
| **GreeksLab (vendor blog)** | total OTM premium about $281 on Fed days vs $55 on normal days (about 5.1x) before the announcement; down 27.9% in the 15 minutes after 14:00 vs 20.8% in the same window on normal days (so the 14:00 "crush" in premium terms is only 7 points larger than normal decay in this window); IV in annualized terms "distorted near expiration" | Order of magnitude for premium, not for IV; source is promotional and the data source is unstated |
| **Johannes, Kaeck, Seeger, "FOMC Event Risk"** (SSRN 4484011) | ex-ante FOMC event risk is "large, time-varying, highly predictive of realized event return volatility, and has a significant variance risk premium" in S&P 500 options | Event risk is priced and time-varying: not captured by a VIX-lagged IV |
| **Almeida, Freire, Hizmeri, "0DTE Asset Pricing"** (SSRN 4701401) | 0DTE variance risk premium is high and driven mainly by upside-risk compensation; "the majority of 0DTEs violates price bounds associated with risk-averse investors"; an exploiting strategy "dissipates after the daily availability of 0DTEs" | The pricing wedge between real 0DTE quotes and a symmetric lognormal model is large and has changed over time (before vs after daily expiries in 2022) |
| **Bandi, Fusari, Reno, "0DTE Option Pricing"** (SSRN 4503344) | Edgeworth-type local-in-time expansion with skewness and kurtosis adjustments gives "significant improvements in the pricing and hedging of 0DTE options as compared to state-of-the-art models" | Even standard stochastic-volatility models misprice ultra-short options, so Black-Scholes is further off. No error figures in the abstract |
| **Alfeus & Mokone, rough volatility for 0DTE SPX** (SSRN 6802138; Finance Research Open 2026) | "classical Markovian stochastic-volatility models struggle to reproduce" 0DTE implied-vol dynamics; nonlinear rough models capture "extreme left-tail skewness and short-dated smile geometry" | Strong smile/skew at the 0DTE horizon that flat-sigma BS cannot generate |
| **Chilingarian, "Three Clocks, One Option"** (SSRN 7205361, 2026) | Evaluating sub-2-week options at a floor-maturity vol misestimates 0-2 DTE gamma by a **median 47%** over 49 real SPX sessions (41 with open interest); error rises with short-end inversion (Spearman 0.53) | Evidence that wrong short-end vol input produces large Greeks errors in 0DTE; it is about Greeks/GEX analytics, not P&L, and uses a small sample |
| **O'Donovan, "0DTE Options and the Price of Tail Protection"** (SSRN 6836498) | The 2022-05-16 daily-expiry launch compressed SPX put skew across tenors (peak 72 bp at 30 days) | Skew regime changed in May 2022: a simulator calibrated on pre-2022 skew is wrong post-2022 |
| **Vilkov (this repo)** | The unconditional mean mid-to-touch change turns SR from +1.06 to -0.61 (put ratio) | A simulator that prices at theoretical mid with no spread overstates P&L by 1.3-4 bp of spot per structure on SPX |
| **M-man2591 repo / Perz** | synthetic chain iron condor Sharpe 8.19, win rate 92% on 38 sessions "simulator artifacts by construction"; Perz finds implied vol overstated vs realized in real SPX 0DTE | Selling premium looks magical in a smooth-price simulator; real quotes carry both an IV premium (an actual effect) and fat tails, spread and fill costs |
| **Beckmeyer, Branger, Gayda** (SSRN 4404704) | retail 0DTE traders lose despite lower effective spreads from the price-improvement mechanism; secondary summaries quote effective spreads of 6.1% (calls) / 4.9% (puts) for retail vs 13.1% / 10.1% non-retail (not verified against the paper) | The cost wedge in % of premium is large for short-dated options even when tight in dollars; relevant to cost modelling, covered in the retail-evidence notes |

What a Black-Scholes + VIX simulator is likely to get wrong, in order of likely importance for a hold-to-close or short-hold strategy (my synthesis, not a measured ranking):
1. **Bid-ask and fills**: the simulator has none. Real cost is 1.3-4 bp of spot for SPX at 10:00 (Vilkov); the SPY/QQQ equivalents need measuring.
2. **Level of ATM IV relative to realized**: the real 0DTE IV usually carries a positive variance risk premium (Vilkov: small, median VRP about 0.0011% of spot; Almeida et al.: high and upside-driven), whereas VIX-derived sigma over- or under-states 0DTE IV depending on term-structure inversion.
3. **Smile/skew and wings**: strong skew at 0DTE (rough-vol papers); matters for spreads, ratio spreads, condors and strangles (the structures with the largest cost and most sensitivity).
4. **Intraday IV path and event days**: Song and GreeksLab show IV accumulating into 14:00 then collapsing; 10:00 entry sees different prices on FOMC/CPI days than on normal days.
5. **Settlement and pin effects**: SPX is cash-settled European; SPY/QQQ are American, physically settled (early exercise and assignment matter for short legs near the close). Not covered by any open study I found.
6. **Regime drift**: Tue/Thu expiries (May 2022) changed skew and the profitability of an exploiting trading rule (O'Donovan; Almeida et al.), so pre- and post-2022 simulated vs real error will differ.

---

## 5. What a developer with your assets can replicate or reuse

You have SPY/QQQ 1-minute shares bars 2016-2026, a BS simulator, and soon real IBKR quotes.

**Replicate now (no option data):**
- Vilkov's realized-moment side: realized variance, upside/downside semivariance and skew from 1-minute bars, lagged features, and the same time stamps (10:00 entry). See `docs/agent-context/variables.md`.
- His protocol (expanding/rolling 252d, logistic L2, hard sign mapping, gross and net reported separately, calibration slope + Brier). Because your option prices are synthetic, only the *gross-signal* side (does 10:00 state predict the sign of the move) is testable without real quotes.
- His correction as a test: run your simulator on his seven structures' unconditional means; real data say mid means are near zero (max |0.016|% of spot) and net negative.

**Needs real quotes (cheapest route):** one month of ThetaData Value ($40) or Massive Advanced ($199) to capture SPY and QQQ 0DTE chains at 10:00 for, say, 3-12 months. Then measure:
1. Half-spread in bp of spot per structure at the entry minute (the single most decision-relevant number, compare to Vilkov's 2.2 bp mean / 1.3-4.0 bp per structure on SPX).
2. Difference between simulator mid and real mid, split by moneyness, time of day, and event days (FOMC/CPI/jobs; the calendar your autopilot already has). That gives the error bound that no paper provides.
3. Intraday ATM IV path vs a lagged-VIX/VIX1D input.

**Do not reuse:** pre-correction Vilkov numbers (SSRN abstract, `docs/`, `paper-claims.csv`, the investment table), thunderscarf's Sharpe 0.97 (mid fills), the M-man2591 options results (synthetic), FlashAlpha content (paid product), the GreeksLab profit tables (zero cost), Option Alpha-based summaries (selection bias).

**Consistent with your memory notes** (signal review, profitability roadmap, external 0DTE evidence): the open evidence supports measuring costs and using paired debit/credit arms; the open-source record does not support any tradeable 10:00 timing edge after costs.

---

## 6. Uncertainties and gaps

- The instruction file `researcher.md` at the specified path did not exist; no other research-protocol file was consulted.
- I did not rerun Vilkov's code (LFS data not fetchable). All numbers are read from shipped tables, code and docs. The `output/tables/` unconditional and OOS tables are post-fix; `0dte_conditional_oos_investment.tex` and all `docs/` summaries are not.
- I did not obtain full text of any SSRN paper (SSRN returned 403). Abstract-level facts come from Crossref abstracts; Beckmeyer et al.'s spread numbers and the Albers-Kestner CPI/FOMC remark are from secondary summaries.
- ThetaData pricing/history discrepancy (docs table says Value options history from 2020-01-01; pricing page says 6 years); ORATS, Cboe DataShop, OptionsDX prices not displayed.
- Not found: any open 0DTE backtest on SPY or QQQ (as opposed to SPX) with real quotes and costs; any open study quantifying BS-with-VIX error in dollars; any audited QuantConnect 0DTE algorithm. Absence in my searches (GitHub CLI multi-word queries often returned nothing) is not proof of absence.
- GitHub star counts and activity are as of 2026-10-20 and the newest repos (July-October 2026) are too young to have been tested by others.

---

## Sources (primary)
- https://github.com/vilkovgr/0dte-strategies (README, KNOWN-ISSUES.md, docs/agent-context/*, docs/paper/paper-annotated.md, output/tables/*.tex, data/conditional_oos_protocol_summary.csv, code/analysis/compute_implementable_pnl.py, compute_conditional_oos_protocol.py; issues #1, #2, #3, #4; commit "Fix transaction-cost unit-scale error in bid-ask half-spread", 2026-08-26)
- https://ssrn.com/abstract=4641356 (abstract via Crossref DOI 10.2139/ssrn.4641356)
- https://github.com/Javier-Garzo/ern-wo-options-backtester (README, docs/research/results/result-tables.md, docs/research/strategies-postmortem/*.md)
- https://github.com/M-man2591/0dte-research-platform (README, LEARNINGS.md, CODE_REVIEW.md)
- https://github.com/thunderscarf/SPX_0DTE_Options_Selling_Public (README, main.py, trades.csv)
- https://github.com/emlama/gex-backtesting; https://github.com/jefrnc/ibkr-odte-strategies; https://github.com/brigoraoul/spy0dte-simulator; https://github.com/askosyrskiy/iron_fly; https://github.com/FlashAlpha-lab/flashalpha-historical-examples; https://github.com/Elias-outofsample/spx-options-pipeline
- https://massive.com/pricing?product=options; https://www.thetadata.net/pricing; https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html; https://datashop.cboe.com/option-quote-intervals; https://databento.com/pricing; https://orats.com/university/historical-data; https://docs.alpaca.markets/docs/historical-option-data; https://optionsdx.com/shop/
- https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/algoseek/us-index-options (and us-equity-options)
- Kaggle public dataset API (search "spy options", "qqq options", "spx options", "option chain")
- Crossref abstracts: Kam 7055179; Perz 7162898; Alfeus-Mokone 6802138; Song 7412318; O'Donovan 6836498; Beckmeyer et al. 4404704; Bandi-Fusari-Reno 4503344; Almeida-Freire-Hizmeri 4701401; Johannes-Kaeck-Seeger 4484011; Chilingarian 7205361; Albers-Kestner (SSRN 4650419 / FRL 2024, doi 10.1016/j.frl.2024.105186)
- https://greekslab.com/blog/trading-0dte-spx-fed-day (vendor blog)
