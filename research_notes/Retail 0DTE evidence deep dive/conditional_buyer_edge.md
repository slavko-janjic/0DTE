# When does buying 0DTE pay? Conditional evidence, 2024-2026 research (as of 2026-10-20)

Scope: conditions under which a long ATM same-day option bought 10:00-10:30 ET, in the direction of the morning move, beats what 0DTE implied volatility priced. Excludes what is already established (Beckmeyer-Branger-Gayda, Vilkov headline, Dim-Eraker-Vilkov, Bandi-Fusari-Reno, Adams et al., Chong, Baltussen, Zarattini) except where I add numbers.

Method note: the instruction file `skills/deep-research/references/researcher.md` named in the brief does not exist at the given path, so I followed the brief's constraints. SSRN, Wiley and ScienceDirect return HTTP 403 to WebFetch, so many papers are abstract-only. Where a PDF was reachable I downloaded and read it with pdftotext (local copies in the scratchpad).

Evidence labels: [FULL] read full text or primary output tables; [ABSTRACT] abstract or seminar listing only; [SECONDARY] someone else's summary; [VENDOR/SELF] self-published tick-data study by a vendor or hobbyist, method stated but not peer reviewed. All hard numbers are SPX/SPXW unless marked SPY. **I found no QQQ/NDX 0DTE evidence at all, and only one SPY study (Wednesdays only, vendor).**

---

## 1. Bottom line

1. **No ex-ante, net-of-cost condition was found under which buying ATM 0DTE has a demonstrated positive expectancy.** Every time a condition was tested on real quotes with costs, the effect was either insignificant, wrong-signed on replication, or too small relative to a per-trade standard deviation near 80% of premium.
2. **Conditions that predict that realized movement will exceed implied do exist, but the option gives most of it back.** The only strongly significant predictor I found is the morning (trailing) realized-to-implied ratio: it predicts the next window's realized/implied ratio with t = +12.8, yet the IV path erases 73-96% of the underlying gain, leaving a net P&L indistinguishable from zero (section 3.3).
3. **The 2022-2026 SPX tape shows "right variance, wrong shape"**: the quoted expected move is roughly unbiased as a variance forecast (E[z^2] = 1.034 +/- 0.062) but the close lands inside 1x expected move 72.4% of the time against 68.3% for a normal. Straddle breakeven (0.8x EM) is exceeded on 38.0% of readings against 42.5% fair. Buyers win less often than fair and need fat-tail days (section 3.1).
4. **The most favorable time window is the one the tool already uses.** Measured 0DTE VRP is lowest at 10:30-11:00 (1.54-1.56) and rises through the day (2.96 at 14:00), and ATM delta-hedged returns are most negative 12:00-14:00 (Almeida et al.). Early-day E[z^2] is about 1.09 versus 0.94-0.99 in the last hours (FirmTape).
5. **FOMC days are the one regime with a large, repeatable realized-over-implied gap** (realized/implied 1.36 vs 0.71 on other days, n = 35), but 10:00 IV is already 17.6% vs 11.3% and ramps to 28.6% by 13:55; whether a 10:00 buyer profits is unresolved and the two sources point in opposite directions (section 3.4).
6. **Directional/momentum conditioning has no net-of-cost support in the 0DTE era.** Unconditional last-half-hour continuation is flat (slope +0.006, t = 0.6, 1,085 days); it reappears only on short-dealer-gamma closes (t = 3.1, 166 days) and does not survive a holdout.

---

## 2. What the newer research adds beyond "retail buyers lose"

| Item | Finding | Label |
|---|---|---|
| Vilkov, *0DTE Trading Rules: Tail Risk, Implementation, and Tactical Timing* (SSRN 4641356, sample 2016-09 to 2026-01, shipped panels to 2024-05) | Long ATM straddle at 10:00, mid prices, 1,318 days: mean PNL -0.0011% of spot, median -0.08%, IQR -0.28% to +0.20%, 99th pct +1.83%, max +6.04%, SD 0.55%, SR -0.03. After the Aug-2026 cost fix no structure keeps a positive net Sharpe (straddle/strangle net SR -0.97). Pre-fix numbers (net SR +0.84 put ratio, +0.93 conditional) are void. | [FULL] shipped output tables from GitHub, not the PDF |
| Almeida, Freire, Hizmeri, *0DTE Asset Pricing* (SSRN 4701401, draft 2025-05-23, sample 2012-01 to 2025-03) | ATM delta-hedged calls and straddles have significantly negative average returns at every entry time (a long-vol buyer loses; most significant 12:00-14:00). Annualized VRP: 1.80 (10:00), 1.56 (10:30), 1.54 (11:00), 1.72, 1.81, 2.21, 1.98, 2.46, 2.96 (14:00); versus 0.56 at 1DTE. After 2022-05-11 the 10:00 VRP is 1.64 vs 1.91 before. The upside component dominates later in the day (VRP+ 0.96 vs VRP- 0.85 at 10:00; 1.97 vs 0.99 at 14:00), the reverse of one-month options where the downside component dominates. The standardized VRP predicts same-day excess market return negatively (10:00 coef -0.049, t -1.7; 11:00 -0.068, t -2.7; R^2 0.4-2.6%). Only ~30% of 0DTEs (6% of ATM) satisfy risk-averse price bounds. | [FULL] |
| Song, *Pre-FOMC Uncertainty Accumulation: Evidence from 0DTE Options* (HEC Montreal, 2026-09-15, 2016-09 to 2023-12) | FOMC-day implied move rises monotonically from 0.60% at 10:00 to 0.78% at the 14:00 release (+30%), then collapses. 71% of event variance resolves at the statement, 29% at the press conference. Implied level predicts realized vol only for windows containing the release (R^2 78-85%). Pre-announcement drift disappears after Wednesday SPX expiries were listed (2016-02-23). | [FULL] |
| Johannes, Kaeck, Seeger (2026), FOMC event risk | Straddles spanning the FOMC *statement* lose significantly; straddles held through the press conference earn about zero. Taken from Song's description only. | [SECONDARY] |
| Maurer and Muller, *Intraday 0DTE Option Order Flow and S&P 500 Downside Risk* (SSRN 7339718, Aug 2026; 1,027 days, 2022-05 to 2026-06) | Put-heavy gamma-weighted put-call ratio at the open is followed by weaker returns over the next hour; out-of-sample trading rule has no significant return before or after costs. | [ABSTRACT] |
| Dorion, Orlowski, Song, *Factor Structure of 0DTE Option Returns* (SSRN 7149778, Jul 2026) | Warehousing gamma and absorbing jump-skewness earn premiums; residual alpha concentrates in 0DTEs but a factor-neutral strategy is infeasible with minimal costs. | [ABSTRACT] |
| Singh, *Inherited State, New Flow, and Intraday Volatility in SPX 0DTE* (SSRN 7350465, Aug 2026) | Tests whether prior gamma state plus new flow predict intraday realized variance; results improve in 2024 and reverse in 2025, so no clean forecasting relation. | [ABSTRACT] |
| Bozovic, *Intraday Jumps and 0DTE Options* (SSRN 5223127) | 22,410 five-minute SPXW observations; jump-risk premium roughly double the diffusion plus vol-risk premia; jump intensity clusters at open and close. | [ABSTRACT] |
| O'Donovan, *0DTE Options and the Price of Tail Protection* (SSRN 6836498, May 2026) | Customer put buying lowers tail-insurance cost by $0.9-1.5bn over 2022-05 to 2024-06; relevant to skew, not to ATM buyers. | [ABSTRACT] |
| Alfeus and Mokone (SSRN 6802138, May 2026) | Rough-vol models on intraday SPX 0DTE surfaces; nonlinear models capture left-tail skew better. Pricing, not trading. | [ABSTRACT] |
| Da, Filippou, Goyenko, Zhang, Zhou, *Intraday Option Return Predictability* (2026-10-02) | Half-hour straddle returns on S&P 500 *constituent stock* options: morning (9:35-10:00) returns mean +29 bps, 15:30-16:00 +19 bps, midday -9 to +2 bps; same-interval momentum and within-day reversal. Delta-neutral straddles on single stocks, 30-day-ish, not index 0DTE. | [FULL] but off-topic for SPY/QQQ 0DTE |
| Bali, Goyal, Morke, Weigert, CFR 26-02 (Dec 2025) | Half-day option-return momentum 0.22-0.45% per half-day; stock options, delta-hedged. Not transferable. | [FULL abstract and intro] |
| Albers (2025, J Futures Markets 45(11)) and Albers and Kestner (2024) | VIX1D overstates next-day S&P 500 volatility (a risk premium); a simple adjustment beats HAR models. VIX1D has a built-in intraday upward drift and overnight bias, so the 10:00 reading is not comparable to the close reading. No VRP magnitude found. | [ABSTRACT] |
| Wilkens (SSRN 7283819), Eurex daily options | Mean implied-minus-realized vol 2.5 and 3.4 annualized points; at the seller's bid the spread falls 48% and 34%, Euro Stoxx 50 only marginally positive. Not US. | [SECONDARY via search snippet] |
| Chong (HKUST, SSRN 4933153, 2020-2024) | 0DTEs expensive but not mispriced; vol-arb Sharpe ratios economically insignificant after costs and estimation risk. | [SECONDARY] |
| Hizmeri and Bevilacqua, *Early Birds Get the Vol* (SSRN 6212458) | Morning VVIX (peak predictive power at 10:00 EST) predicts next-day variance-asset returns. VIX options, not 0DTE. | [ABSTRACT] |

---

## 3. Conditional evidence by variable

### 3.1 Unconditional baseline on the 2022-2026 tape (needed to read the conditions)

- **FirmTape, expected-move calibration** [VENDOR/SELF]. SPX, 1,088 sessions, 2022-04-14 to 2026-08-26, 82,378 five-minute readings (>=15 minutes to close), day-clustered SEs. Close inside 1.0x EM: 72.4% +/- 0.9 (normal 68.3%). Inside 0.8x EM (straddle breakeven): 62.0% (57.5%). Inside 0.5x EM: 42.4% (38.3%). E[z^2] = 1.034 +/- 0.062; QLIKE of implied 1.50 vs trailing realized 2.05. By time of day coverage is 71.0% for open-10:30 and 71-74% elsewhere; by year 71.4-74.2%. E[z^2] about 1.09 in the first hours, 0.94-0.99 in the last hours. Interpretation: variance roughly fair, center too fat, tails fat. Gamma-state gradient (74.5% vs 71.0%) is 2-3 SE and flagged by the author as unreliable.
- **FlashAlpha, SPY Wednesdays** [VENDOR/SELF, paid product; mids, no costs]. 193 sessions, 2022-07-06 to 2026-04-01, 10:00 ATM straddle vs 16:00 close. Inside 1 sigma: 71.0% (fair 68.3%), breaches 25.9% (31.7%), median realized/implied 0.589 (fair 0.674), median implied 0.72% of spot, median |move| 0.37%. By year hit rate 69.2/69.2/70.0/71.2/84.6% (2026 has 13 sessions). Worst miss 2025-04-09: implied 3.51%, SPY +8.72% (2.5x). The article's own "74%" figure does not reconcile with its 71.0% + 25.9%. Medians understate the buyer's mean payoff, because the straddle payoff is convex; use the tail-day frequency, not the median.
- **Vilkov unconditional** (above): long ATM straddle at mid is about zero in mean with a median loss of 0.08% of spot; loss probability about 70% (after cost, averaged over moneyness combos); worst day -2.67% of spot.
- **Costs have shrunk since the Vilkov sample.** FirmTape measures near-ATM SPXW half-spreads of 0.0028% of spot on its 2026-05 to 2026-07 tape vs 0.0178% in Vilkov's panel (6.4x tighter); round-trip friction on an ATM straddle is $35 on a $2,260-2,440 premium (1.5%), 13-22% of the mean absolute mid-to-mid P&L at 15-30 minute holds [VENDOR/SELF, 61 sessions]. SPY/QQQ spreads: unmeasured in any source.

### 3.2 VIX level / realized-volatility regime

| Source | Result | Label |
|---|---|---|
| Vilkov, VIX regime terciles of the 10:00 integrated 0DTE implied variance (940 days, % of spot, mid) | Strangle/straddle (long): low -0.0018, mid +0.0001, high -0.0160; High-Low -0.0142, t -0.44. Put ratio spread High-Low +0.078 (t 2.50, p 0.013); bear put +0.045 (t 1.19); risk reversal +0.044 (t 1.20); call spreads better in low VIX (n.s.). FirmTape: a single t = 2.5 cell among 7 does not survive Bonferroni, and no structure's tercile gap beats permutation noise on its tape (best-of-seven p >= 0.96). | [FULL tables] |
| Almeida et al. Table 7 (after-cost Sharpe of their SSD-violation trade, *not* a plain buy) | Low realized variance 0.40-0.47 vs High 0.00-0.08; low 0DTE volume and low Google attention also better; VRP level barely matters. | [FULL] |
| FirmTape replication of that sort (trailing 5-session realized vol, expanding median, 1,008 sessions, 10:00 entry, honest fills) | Low minus High for short straddle/strangle/condor: all nine differences negative (-0.01 to -0.05 per trade), none significant; claimed +0.32 to +0.47 not reproduced (min detectable 0.19). | [VENDOR/SELF] |
| Thetadesk, 20 years of SPX closes, *30-day VIX* band applied to 1-day moves | Calm regimes: 1-day hit rate about 86%, implied/realized about 2x. VIX > 30: hit rate 69.5%, implied/realized 1.19. Premium compresses in stress at the daily horizon. Not 0DTE IV, not intraday, no option P&L. | [SECONDARY, blog with stated method] |
| OptionMetrics (10:00 ex-ante 0DTE VRP, Friday expiries only, 399 sessions from 2018) | SPX mean VRP 0.0028, median 0.0021, skew +1.33; realized variance occasionally exceeds implied after elevated prior-day volatility (shown for TSLA, no numbers). | [SECONDARY] |
| Fattail.ai | Coverage 70-75% when VIX < 17 and more large breaches above 25; no data shown. | [VENDOR, unsourced] |

Takeaway: "buy when VIX is high" is not supported by the one cell-level table available (the high-VIX tercile is the *worst* for a long straddle at mid, n.s.), while the daily-horizon VIX evidence says the premium shrinks in stress. These point in opposite directions and neither is a net-of-cost buyer result. Treat VIX level as undecided.

### 3.3 Implied versus realized by intraday state (the closest thing to a signal)

FirmTape, *What a 0DTE straddle round trip costs* [VENDOR/SELF; 61 sessions 2026-05-01 to 2026-07-31; 4,209 round trips; one calm regime; day-clustered SEs; design effect 6.8, effective n about 544]:

- Trailing 30-minute realized move divided by implied move (k = 30) predicts the following 30-minute realized/implied ratio: coefficient +0.486, t +12.84 (+0.352, t +6.05 with time-of-day dummies). Decile means run 0.697 (quietest) to 1.352 (busiest). Realized exceeds implied in **9%** of bottom-decile windows and **85%** of top-decile windows.
- But P&L is not predicted. Unhedged long straddle, mid fills: 15m -$4.48 (t -0.52), 30m -$0.66 (t -0.04), 60m +$10.03 (t +0.23). At touch: -$34.21 (t -4.18), -$30.79 (t -1.89), -$21.26 (t -0.49).
- Decomposition (all straddles): underlying plus decay +$13/+$28/+$57 (15/30/60 min) offset by IV path -$13/-$24/-$42; IV path cancels 96%, 85%, 73% of the underlying leg. Median IV falls in 58.2% of windows.
- Top decile, 60-minute hold: underlying +$324, IV path -$130, spread -$35, fees -$5, net about +$152 (SE about $211, about 6.5% of a $2,350 premium).
- Delta-hedged, across 780 hedged and unhedged cells, none clears t = +2 and 91.5% are negative.
- Caveat: the author's own pre-cost number is +$42.8 (SE $41.9) and the lower break-even bound is negative.

Implication for the tool: "morning realized exceeded implied" is a real volatility-clustering fact and is a legitimate condition to log, but it must be evaluated on *option P&L*, not on the underlying move, because dealers reprice IV after the same information. If the morning-move filter correlates with this state, the filter selects busier-than-priced windows and also higher entry IV.

### 3.4 Event days (FOMC, CPI, jobs)

- **Timing for a 10:00-10:30 entry:** CPI and jobs (08:30) are already in the price. The relevant scheduled events are FOMC (14:00 statement, 14:30 press conference) and 10:00 releases (ISM, consumer confidence, JOLTS). No source tests the 10:00 releases.
- **FirmTape, FOMC vs other days** [VENDOR/SELF, 1,083 sessions 2022-04-14 to 2026-09-02; 35 decision days; ~37 tests, mostly pre-registered, 15 post hoc]. Realized/implied (30-minute window read at 15:00): **1.36 vs 0.71** (diff +0.65, t +8.84). ATM IV at 10:00: 17.63% vs 11.32%; at 13:55: 28.59% vs 10.61%. Range 14:00-15:00: 47.2 vs 16.1 points; range/sigma 5.65 vs 1.62. **No IV crush**: ATM IV change 13:55->14:35 +1.22% vs +3.31% (t -1.02); straddle change -16.6% vs -14.8% (t -1.07). Direction not predictable (book sign vs 14:00-14:30 direction 51%, t 0.10). Post hoc (not claimed): last-hour sell-off average -18.2 points (t -2.85, one day -125).
- **Song** [FULL]: implied FOMC move 0.60% at 10:00 -> 0.78% at the release; implied uncertainty is not a pure risk premium (forecasts event-window realized vol with R^2 78-85%).
- **Almeida et al.** [FULL]: 68 FOMC days; FOMC days raise average VRP; delta-hedged ATM options earn mostly negative average returns *before* the announcement and positive around it, none significant (n small). Removing FOMC days leaves the main results essentially unchanged.
- **Johannes-Kaeck-Seeger via Song** [SECONDARY]: straddles spanning the statement lose significantly.
- **Wright, NBER w28306 (2020)** [FULL]: one-day-to-expiry E-mini options (Wed/Fri, not true 0DTE, overnight-before snapshot, 2017-2020). Average VRP positive and significant, bigger for stocks on FOMC days than on jobs days; S&P futures table: FOMC coefficient about 0.25, jobs about 0.20 (table text garbled in my extraction, units not confirmed). Probability integral transforms show the implied distribution too wide for Treasuries but some upside tail misses for stock futures on FOMC days.
- **Delaware dissertation** [SECONDARY via search snippet]: delta-neutral S&P straddles positive on FOMC days, negative on CPI/NFP days; sample and horizon not verified. OptionMetrics note: index straddle premia negative around announcements except FOMC.

Reading: FOMC afternoons are the one case with a large measured realized-over-implied gap, but the market raises IV all morning (Song) and the 10:00 buyer pays that ramp. The tool's entry window sits before the ramp. **Whether a 10:00 buyer who exits before 14:00 harvests the IV ramp is not tested anywhere and Almeida's pre-announcement evidence is negative (n.s.).** With about 8 FOMC days a year, any test accumulates 8 observations per year; use it as a monitored flag only.

### 3.5 Overnight gap, prior-day realized vs implied, VIX term structure, day of week

- **Gap size:** no rigorous 0DTE study. Vendor table (thetrading.tools, SPY, 1993-present, unverified): same-day gap fill rates 78% (0.1-0.25% up-gap), 46% (0.5-1%), 26% (1-2%); a 1-2% up-gap closes up 60% of the time (n = 289). CXO's gap study (SPY 1993-2025) is paywalled. Song: the FOMC pre-announcement drift (0.21% over 24h) sits in the overnight gap (0.19%), not the session.
- **Prior-day realized vs implied:** FirmTape's closest test is the trailing 5-session realized-vol split above (null for short premium). OptionMetrics gives only a qualitative statement.
- **VIX9D/VIX or VIX1D/VIX9D inversion:** no study at the 0DTE horizon. Albers/Albers-Kestner say VIX1D has a mechanical intraday drift and overnight bias. FlashAlpha (vendor, weekly 2018+) reports a 4-week-ahead outcome distribution about 60% wider in backwardation. Hypothesis only.
- **Day of week:** no 0DTE buyer-vs-seller study. Papagelis and Dotsis (2024/2025 JFM, VIX-style index data) report that buying volatility at the open and selling at the close was profitable most days except Fridays [SECONDARY, no magnitudes]. OptionMetrics (1DTE short puts, not 0DTE): Monday mean 11.3 bp, a weekend premium for sellers. Option Alpha: Monday best for iron-butterfly sellers, Thu worst, "not statistically significant" by its own words. Papagelis-Dotsis also report the VRP is negative overnight and turns positive and often insignificant intraday, i.e. the intraday window is the cheapest place to be long vol.
- **Time of day:** Section 2 (Almeida VRP by entry time) and 3.1 (FirmTape E[z^2] by time). Da et al. find half-hour straddle returns on stock options are about zero midday with larger positive 9:35-10:00 and 15:30-16:00 means (+29, +19 bps); not index 0DTE.
- **Replication warning:** FirmTape could not reproduce Muravyev and Ni's day/night option-return split on SPX (5-30 DTE, 1,082 sessions) and attributes about 70% of the split to the closing mark. Do not lean on "intraday option returns are positive."

### 3.6 Directional trades and trend-day identification

- **Vilkov** [FULL tables, mid prices]: ATM-long directional spreads have typically negative median PNL; ATM-to-OTM calls and most puts lose on average per unit of spot; deep ITM contracts have IQR 0.7-0.8% of spot; risk reversal is the only structure with positive mean, median and 25th percentile, about 0.01% of spot (0.24 bp net of cost, loss probability 52.7%, ES1% 1.64%). Realized skewness of the underlying drives most P&L (R^2 of realized variance alone <= 4.5%). Structural break around the 2022 daily-expiry date is not significant for any structure (p >= 0.22).
- **FirmTape, intraday momentum retest** [VENDOR/SELF; 1,085 SPX days 2022-04 to 2026-08]: regress last-30-minute return on open-to-15:30 return: slope +0.006 +/- 0.009 (t 0.6); by year +0.009, +0.002, -0.016, +0.016, -0.012. Conditional on tape-signed dealer book being short at 15:30 (166 days, about 15%): interaction +0.055 (t 3.1), net slope +0.038; open-interest-convention book shows nothing (t -0.4); final 40-session holdout has 4 short closes and does not survive leave-one-out; costs not analyzed. Gao-Han-Li-Zhou (JFE 2018, 1993-2013 SPY) found R^2 1.6% for first-half-hour -> last-half-hour, stronger on high-volatility, high-volume, macro-news days; that sample is pre-0DTE.
- **Maurer and Muller** [ABSTRACT]: open-flow PCR signal has no OOS trading return.
- **Trend-day identifiers (wide opening range, early volume, gap-and-go):** no peer-reviewed or tick-tape test found. Vendor/blog claims are untestable: Edgeful via Trade That Swing says SPY breaking above the 15-minute opening range closes green 64.4% (about 70% as of Mar 2026); Option Alpha says 85% of small opening moves ended the day higher (avg +0.65% 10:30 to close) and 62-67% of medium moves mean-reverted. Methodology, sample and costs unknown; do not treat as evidence.
- **Touch probabilities (FirmTape, 1,086 days, no options)** [VENDOR/SELF]: using trailing 30-minute realized sigma, a level 1.12 sigma away with 60-120 minutes left was reached 28.4% of the time; 2.25 sigma 2.5-9.9%; 5 sigma 0.2-1.2%. Opening-half-hour sigma overstates the rest of the day (reach 15.6% at 1.12 sigma at the open). Useful baseline for any "morning move continues" claim.
- **Almeida, Maurer-Muller, Song agree on one thing:** the upside-risk premium is expensive and the upside component carries the negative same-day return predictability (Almeida, t roughly -2 to -4 across entry times; my reading of a garbled regression table, so treat the range as approximate). If you buy calls after an up-morning you are paying the richest part of the surface.

---

## 4. Candidate conditions ranked for a developer's test plan

Ranked by prior evidence of a *realized > implied* link, penalized by net-of-cost evidence.

| Rank | Condition to log and test | Prior evidence | Expected size | Verdict |
|---|---|---|---|---|
| 1 | Morning realized move / 10:00 implied move (state ratio), measured on option P&L not underlying | FirmTape t +12.8 on forward realized/implied; deciles 0.70-1.35 | Gross +$152 on a $2,350 straddle in top decile at 60m (SE $211), IV path takes 73-96% | Log it; do not gate on it. Test on entry IV-adjusted P&L |
| 2 | FOMC days (14:00 release), exit before vs after the statement | Realized/implied 1.36 vs 0.71 afternoons; IV 17.6% -> 28.6% | n = 8 per year; sign of 10:00 buyer P&L unresolved (Song/JKS negative across statement; Almeida pre-announcement negative n.s.) | Monitored flag only; no power in under 3-4 years |
| 3 | Entry window 10:00-11:00 vs later | VRP 1.54-1.80 vs 2.96 at 14:00; E[z^2] 1.09 early vs about 0.95 late | VRP gap about 1 annualized pt; spreads minimal 10:00-14:00 | Current 10:00-10:30 window is the best-supported; paired 10:30 vs 11:00 arm is cheap |
| 4 | High vs low realized volatility (rv5) regime | Almeida (SSD trade, pre-2022) vs FirmTape null | Differences -0.03 to -0.05 per trade, n.s. | Not supported |
| 5 | VIX level | Vilkov tercile: high-VIX worst for long straddle (n.s.); Thetadesk: premium compresses at VIX > 30 | Opposite signs | Undecided; do not pre-commit direction |
| 6 | Dealer gamma sign (tape-signed) | Momentum t 3.1, holdout fails | n/a | Not available to a retail data feed; skip |
| 7 | Gap size, day of week, VIX9D/VIX | No 0DTE study | n/a | Low prior; multiple-testing cost exceeds value |

Power arithmetic (Vilkov SD of long-ATM straddle PNL about 0.55% of spot, FirmTape per-trade SD about 80% of premium): to see t = 2 on a mean of +0.05% of spot you need n = (2 x 0.55 / 0.05)^2, about 480 trades per bucket; a +5% of premium edge needs about 1,000 trades. A split into thirds multiplies that. Do not read buckets with n < 300.

---

## 5. Where the sources disagree

- **Is there a variance premium for buyers to overcome?** Almeida (2012-2025) and Vilkov: yes, positive and mostly upside, with ATM delta-hedged returns significantly negative. FirmTape (2022-2026): E[z^2] = 1.034 +/- 0.062, no second-moment premium; the loss for buyers comes from shape (more central mass) and spreads. These are compatible only if the premium sits in the center/upside of the distribution and was larger before 2022; Almeida's post-2022 VRP at 10:00 (1.64) is not much smaller than the pre-2022 value (1.91).
- **Medians vs means:** FlashAlpha median realized/implied 0.589 looks like heavy overpricing; FirmTape mean-square ratio 1.03-1.09 looks fair. The buyer's payoff depends on tails, so both are right.
- **Costs:** Vilkov's post-fix 2.2 bp mean half-spread (2016-2024 panel, averaged over strikes and years) vs FirmTape's 0.0028% of spot near the money on 2026 SPXW. Do not import either for SPY or QQQ.
- **FOMC pre-announcement P&L:** Almeida (negative, n.s.), JKS via Song (negative spanning the statement), Delaware dissertation (positive, unverified) and FirmTape (no IV crush) cannot be reconciled from these summaries.

## 6. Limits and what I could not read

- **Full text read:** Almeida et al. (FMA 2025 draft, incl. online appendix tables), Song (2026-09-15), Wright NBER w28306 (table extraction partly garbled), Da et al. (2026-10-02), Bali et al. CFR 26-02 (abstract/intro), Vilkov replication package (README, KNOWN-ISSUES, annotated paper text, output tables), FirmTape pages cited.
- **Abstract or snippet only:** Maurer-Muller, Dorion-Orlowski-Song, Singh, Bozovic, O'Donovan, Alfeus-Mokone, Hizmeri-Bevilacqua, Wilkens, Albers, Papagelis-Dotsis, Johannes-Kaeck-Seeger, Chong (HKUST write-up).
- **Not found:** any QQQ/NDX study; any 0DTE study conditioning on overnight gap, VIX9D/VIX, or 10:00 data releases; any peer-reviewed trend-day test; Cboe customer P&L by condition (Cboe's "0DTEs Decoded" has no P&L; says 95% of 0DTE trades use limited-risk structures, retail share 50-60%).
- **Vendor caveat:** FirmTape and FlashAlpha sell data/dashboards. Their studies state methods and are mostly self-critical, but short samples (61 sessions for the cost study, 35 FOMC days, 193 Wednesdays) and single regimes limit them. The Vilkov replication package itself was corrected by an outside team, and one FirmTape comment (that the Vilkov straddle sign differs on their 46-session tape) rests on a calm 2026 summer.
- **Companion note:** `open_code_data.md` in this folder already covers the Vilkov repo and data sources in detail.

## 7. Sources

- Almeida, Freire, Hizmeri, 0DTE Asset Pricing: https://www.fma.org/assets/docs/Derivatives2025/Almeida.pdf (SSRN 4701401)
- Song, Pre-FOMC Uncertainty Accumulation: https://afajof.org/management/viewp.php?n=260400
- Vilkov replication package: https://github.com/vilkovgr/0dte-strategies (README, KNOWN-ISSUES.md, output/tables/0dte_stratret2000_2100_main.tex, 0dte_vix_regime_1000.tex, 0dte_conditional_oos.tex, 0dte_implementable_pnl.tex, 0dte_tail_risk_diagnostics.tex, 0dte_structbreak_post2022.tex; docs/paper/paper-annotated.md)
- Da et al., Intraday Option Return Predictability: https://academicweb.nd.edu/~zda/IntraOption.pdf
- Bali et al., CFR 26-02: https://cfr-cologne.de/download/workingpaper/cfr-26-02.pdf
- Wright, Event-Day Options: https://www.nber.org/system/files/working_papers/w28306/w28306.pdf
- SSRN listings (abstract only via search): Maurer-Muller 7339718; Dorion-Orlowski-Song 7149778; Singh 7350465; Bozovic 5223127; O'Donovan 6836498; Alfeus-Mokone 6802138; Hizmeri-Bevilacqua 6212458; Wilkens 7283819; Chong 4933153 (https://bm.hkust.edu.hk/bizinsight/2026/04/are-0dte-options-mispriced)
- Albers (2025): https://ideas.repec.org/a/wly/jfutmk/v45y2025i11p2092-2108.html ; Albers and Kestner (2024): https://www.sciencedirect.com/science/article/pii/S1544612324002162
- FirmTape (vendor): https://firmtape.com/research/how-accurate-is-the-expected-move ; /what-a-0dte-straddle-round-trip-costs ; /what-the-fomc-decision-does-to-the-book ; /the-fomc-rules-read-forward ; /is-intraday-momentum-still-alive ; /does-a-volatility-state-rescue-0dte-selling ; /do-spx-option-returns-change-sign-from-day-to-night ; /vilkov-0dte-rules-replication ; /touch-probability-surface ; /what-the-0dte-literature-claims
- FlashAlpha SPY study (vendor): https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study
- Thetadesk (blog): https://thetadesk.substack.com/p/how-often-does-the-expected-move
- OptionMetrics 0DTE VRP post: https://optionmetrics.com/?p=3155 ; Hull Tactical on Vilkov: https://hulltactical.com/2026/07/08/0dte-options-tiny-edge-huge-distribution/
- Harbourfront Quant on Papagelis and Dotsis: https://harbourfrontquant.substack.com/p/breaking-down-the-volatility-risk
- Cboe 0DTEs Decoded: https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact
- Gap table (unverified): https://www.thetrading.tools/gap-analysis ; CXO gap study (paywalled): https://cxoadvisory.com/technical-trading/gap-reversal-or-continuation
