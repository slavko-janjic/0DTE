# On SPY/QQQ, 0DTE costs are small, fees are not, and no tested condition makes buying pay

Drilling into the 2024-2026 literature, the SPY/QQQ cost data and the open code leaves the baseline conclusion intact and moves its weight. I found no condition (volatility regime, event day, gap, day of week, intraday state) under which buying an ATM same-day option has a demonstrated positive expectancy net of costs. The nearest candidate, a morning realized-to-implied state, predicts that the underlying will outrun the option's price with t = +12.8, yet dealers re-price implied volatility on the same information and erase 73-96% of the underlying gain, leaving net P&L near zero ([FirmTape](https://firmtape.com/research/what-a-0dte-straddle-round-trip-costs), vendor study, 61 sessions). Two parts of the baseline are overturned. Spread is **not** the main cost channel for SPY/QQQ at 10:00: your own quote log shows SPY ATM is 1 cent wide 95% of the time and QQQ 82%, about 0.9% of premium round trip, so your simulator's 1 cent is right for SPY and about 25% light for QQQ. The cost the simulator misses is the broker fee: at IBKR's $1.00 per-order minimum (secondary source, verify), a 1-lot round trip costs about $2.00 against a $123 SPY premium, which with the spread brings total friction to roughly **2.1-2.6% of premium, the same size as the +2-3% relative edge**. The baseline's two structure ideas also weaken: narrow debit verticals cost 4-8x more per unit of delta than a single, and a debit call spread and a credit put spread at the same strikes are near-synthetic twins, so "credit beats debit" in the retail data describes which strikes people sell, not a structural free lunch. The baseline's 1,000-trade estimate holds, but the 40-day rule has only about a 5% chance of passing even if the simulated edge is real, so it cannot confirm anything. The decisive test is not another paper arm; it is replaying the morning family on real SPY/QQQ quotes (one month of a $40-$199 data subscription), which is the only way to learn the absolute net level.

*How this was built:* I synthesized three research notes (conditional evidence, SPY/QQQ costs and structures, open code and data) and the baseline report. I did not re-fetch primary sources, so figures are as recorded in the notes, with their evidence labels carried through. Legend: **[A]** regulator or peer-reviewed text read in full; **[B]** working paper or shipped data tables read; **[C]** vendor or hobbyist study with a stated method; **[D]** abstract or secondary summary only; **[M]** my arithmetic or model, an inference and not evidence.

---

## 1. Scorecard against the baseline

| Baseline claim | Verdict | What the new evidence says |
|---|---|---|
| 1 cent is the optimistic end of ATM spread assumptions; vendors say 2-5 cents | **Overturned for SPY, mild for QQQ** | Quote log, 37 sessions, delayed Yahoo quotes: SPY 1 cent 95.4% of quotes, QQQ 81.8%. Mean ATM spread 1.08 cents (SPY) and 1.35 (QQQ) at 10:00-10:30. Vendor "$0.02-0.05" and "$0.15-0.40 in the last 15 minutes" do not appear through 15:46 [M, project data]. |
| At a $2 premium a 2-5 cent round trip is 1-2.5% of premium | **Sharpened** | The measured 10:00 premium is $1.23 (SPY) and $1.65 (QQQ), not $2. Spread alone is about 0.9% of premium. Fees, absent from the baseline, are 1.3-1.7% for a 1-lot at IBKR. |
| Buyers lose mostly to the spread (Beckmeyer: over $90M of $125M) | **Narrowed to SPX** | SPX ticks are 5-10 cents; SPY/QQQ tick is 1 cent at every price. SPY/QQQ spreads are roughly 10-15x tighter per unit of notional than SPXW [M]. Buyers still lose at mid in the academic data (ATM delta-hedged options earn significantly negative returns at every entry time, [Almeida et al. 2025](https://www.fma.org/assets/docs/Derivatives2025/Almeida.pdf) [B]). For ETFs the loss comes more from the premium and the IV path, plus fees. |
| Rank 2: debit vertical vs single | **Downgraded** | Cost per unit of delta, round trip, SPY: single ATM 2.0 cents, $1 vertical 15.3, $2 vertical 8.1, $5 vertical 4.4 [M]. Only a $5 vertical is within 2x of a single, and it gives up gamma. |
| Rank 3: credit spread in signal direction ("strongest literature-backed") | **Downgraded** | Put-call parity makes a bull put credit spread and a bull call debit spread at the same strikes near-identical positions [M]. Beckmeyer's credit/debit split reflects mostly OTM sold wings earning the variance premium. Vilkov's corrected net Sharpes are negative for every structure except a risk reversal at +0.10 ([KNOWN-ISSUES](https://github.com/vilkovgr/0dte-strategies/blob/main/KNOWN-ISSUES.md) [B]). |
| Rank 4-5: VIX split, price-based gamma split | **Dropped** | VIX direction is contradictory (high-VIX tercile worst for a long straddle, n.s.; premium compresses in stress at the daily horizon). Dealer-gamma momentum is a closing-half-hour effect that fails a holdout (section 3). |
| Rank 6: entry-price richness as covariate | **Promoted** | The only variable with a strong volatility-clustering result (t = +12.8). Log it, because it decides whether the morning filter is selecting high-IV entries. |
| 1,000 trades for t = 2 on +2.5% | **Confirmed, with a caveat** | Holds if per-trade SD is about 40% of premium (bounded exits). Unbounded holds have SD near 80% [C], which would need about 4,100 trades. Trades on one day are correlated, so count days. |
| 40-day rule is a loss detector | **Confirmed and quantified** | Power to pass at t >= 2 given a true +2.5% edge is about 5%; the false-pass rate is about 2% (section 6, item 2) [M]. |
| Keep the number of looks small | **Confirmed** | Section 6 sets a budget of three confirmatory tests, threshold t about 2.4 [M]. |

---

## 2. How developers build these tools, and what the open code shows

The baseline mapped the ecosystem (no-code bots, broker-API stacks, paid signal services, mostly premium selling). The new notes add what the open code actually contains. Almost none of it supports a trading edge, and several repos are valuable mainly as warnings.

| Repo or source | Data and costs | Claim | Read |
|---|---|---|---|
| [vilkovgr/0dte-strategies](https://github.com/vilkovgr/0dte-strategies) (SSRN 4641356) | Cboe 30-minute SPXW NBBO bars, 2016-2026 sample, half-spread per leg plus 0.5 bp | After an Aug 2026 unit-error fix, "no strategy or basket retains a positive net Sharpe" | Best inspectable academic-grade test [B]. Panels end 2024-05-01, only about two years have daily expiries, Git LFS budget exceeded so the panels cannot currently be fetched, and the SSRN abstract and `docs/` still quote pre-fix numbers |
| [Javier-Garzo/ern-wo-options-backtester](https://github.com/Javier-Garzo/ern-wo-options-backtester) | ThetaData 5-minute NBBO, fills at 60-65% of spread | 0DTE-only naked puts: 0.13% annualized; 0DTE+1DTE: 1.55% against the original author's live 7.06% CAGR | Best non-academic repo [C]. A sophisticated retail strategy replicated on real quotes at roughly a fifth of its reported return (periods and implementation differ, so indicative only) |
| [thunderscarf/SPX_0DTE_Options_Selling_Public](https://github.com/thunderscarf/SPX_0DTE_Options_Selling_Public) | Polygon chains, fills at mid, no costs | Sharpe 0.97, 78% win rate | Not evidence. Vilkov's mid-price Sharpes of +0.5 to +1.06 turned negative at the touch |
| [M-man2591/0dte-research-platform](https://github.com/M-man2591/0dte-research-platform) | Synthetic Black-Scholes chain, 38 sessions | Iron condor Sharpe 8.19, labelled "simulator artifacts by construction" | A useful negative example for your own simulator |
| [emlama/gex-backtesting](https://github.com/emlama/gex-backtesting) | 513 days of SPX 0DTE trades (Polygon flat files) | GEX concentration vs late-day put spikes | Sound hygiene (pre-registration, FDR) but a hypothesis test with a custom proxy, no P&L |
| FlashAlpha repos and articles | Paid product (Historical API $1,499/month) | Many "backtest essays" | Marketing; their SPY data study is the only SPY study found and is Wednesdays only |

Two generalizations. First, the honest projects are the ones that report their own failures (Vilkov's correction, the M-man2591 code review, Garzo's postmortems), and each shows the same pattern: results shrink or flip when fills are realistic. Second, I found no open 0DTE backtest on SPY or QQQ with real quotes and costs, and no QQQ or NDX 0DTE study of any kind. Your tool's quote log is, as far as I can tell, a rarer dataset than most of what is public. The absence is a limit on my search, not proof.

---

## 3. (a) When, if ever, does buying 0DTE pay?

### What the 2024-2026 research adds

| Paper | Finding | Grade | Relevance to a 10:00-10:30 directional buyer |
|---|---|---|---|
| [Almeida, Freire, Hizmeri, 0DTE Asset Pricing](https://www.fma.org/assets/docs/Derivatives2025/Almeida.pdf) (2012-2025) | ATM delta-hedged calls and straddles lose significantly at every entry time, most at 12:00-14:00. Annualized VRP 1.80 (10:00), 1.54-1.56 (10:30-11:00), 2.96 (14:00). Upside component dominates later in the day. The "mispricing" trade dissipated after daily expiries | B | Your 10:00-10:30 window is the cheapest part of the day to be long, and still negative |
| Vilkov, *0DTE Trading Rules* (corrected Aug 2026) | Long ATM straddle at 10:00 held to close, 1,318 days, mid: mean -0.0011% of spot, median -0.08%, SD 0.55%. No structure keeps positive net Sharpe | B | Blind mid-price buying is about zero before costs, not positive |
| [Song, Pre-FOMC Uncertainty](https://afajof.org/management/viewp.php?n=260400) (2026) | FOMC-day implied move rises monotonically 0.60% (10:00) to 0.78% (14:00 release) | B | A 10:00 buyer pays the ramp; whether holding through it pays is untested |
| FirmTape studies (2022-2026 SPX tape) | See below | C | Calibration, state ratio, costs, FOMC, momentum |
| [Da et al.](https://academicweb.nd.edu/~zda/IntraOption.pdf) (Oct 2026), Bali et al. (CFR 26-02) | Half-hour straddle return momentum and a positive 9:35-10:00 mean (+29 bps) | B | Single-stock options, delta-neutral, 30-day-ish. Not transferable |
| Maurer-Muller, Dorion et al., Singh, Bozovic, O'Donovan, Hizmeri-Bevilacqua, Albers | Order-flow signals with no out-of-sample trading return; factor residual alpha infeasible after costs; gamma-state relations that reverse in 2025; VIX1D has a built-in intraday drift | D | None supports a tradeable rule |
| [Chong (HKUST)](https://bm.hkust.edu.hk/bizinsight/2026/04/are-0dte-options-mispriced) | Expensive but not mispriced | D | Same as the baseline |

### The unconditional shape: buyers need tails

On the 2022-2026 SPX tape the quoted expected move is roughly fair as a variance forecast (E[z^2] = 1.034 +/- 0.062), but the close lands inside 1x expected move 72.4% of the time against 68.3% for a normal, and the straddle breakeven is exceeded 38.0% of the time against 42.5% fair ([FirmTape](https://firmtape.com/research/how-accurate-is-the-expected-move), C). The SPY Wednesday study finds the same 71.0% inside one sigma ([FlashAlpha](https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study), C). The baseline cited that study's "realized about 13% below implied"; that is a **median** ratio. Because option payoffs are convex, the buyer's mean depends on tail days (April 9, 2025: implied 3.51%, SPY +8.72%), and the mean-square ratio is about 1.03-1.09. Read vendor "overpriced" headlines as saying buyers win less often than fair, not that they lose a fixed 13%.

### Conditions, ranked by evidence, none with demonstrated net edge

| Condition | Best evidence | Verdict for your tool |
|---|---|---|
| **Morning realized / implied state** | Forward realized/implied rises 0.70 to 1.35 across deciles (t +12.8); realized beats implied in 85% of top-decile windows. But IV path cancels 96%, 85%, 73% of the underlying gain at 15/30/60-minute holds; top decile 60-minute net about +$152 on a $2,350 straddle, SE about $211; 91.5% of 780 hedged/unhedged cells negative [C] | Real clustering, priced away. Log as a covariate; do not gate on it |
| **Time of day** | VRP lowest 10:30-11:00; E[z^2] about 1.09 early vs about 0.95 late; spreads minimal 10:00-14:00 [B, C] | Supports your current window; nothing supports later entries |
| **FOMC days** | Realized/implied 1.36 vs 0.71 in afternoon windows (35 days, t +8.84), but 10:00 IV is already 17.6% vs 11.3% and ramps to 28.6% by 13:55; no IV crush; direction unpredictable [C]. Straddles spanning the statement lose significantly (Johannes-Kaeck-Seeger, via Song [D]) | Sign for a 10:00 buyer unresolved; about 8 days a year means no power for years. Tag only |
| **Realized-volatility regime** | Almeida's low-vol advantage applies to a short-volatility trade and is not reproduced post-2022 (all nine differences negative, n.s.) [B, C] | Not supported |
| **VIX level** | Vilkov: high-VIX tercile worst for long straddle (n.s.); only significant cell is a put ratio spread (t 2.50, one of 7, fails Bonferroni). Daily-horizon blog: premium compresses at VIX > 30 [B, D] | Undecided; do not pre-commit a direction |
| **Intraday momentum into the close** | Last-half-hour slope +0.006 (t 0.6, 1,085 days); appears only on short-dealer-gamma closes (t 3.1, 166 days) and fails a holdout [C]. Baltussen's first-half-hour predictor had negative out-of-sample R-squared (baseline) | Not tradeable with options after costs |
| **Gap size, day of week, VIX9D/VIX** | No 0DTE study. Vendor gap-fill tables unverified [D] | Skip |
| **Trend-day identifiers (ORB, gap-and-go)** | No peer-reviewed or tick-tape test. Vendor claims such as "64-70% close green after a break" have unknown method and no costs [D] | Your own sim (+2-3%, fading) is the only evidence; treat as a hypothesis |

### Two details that bear directly on a morning-drive call buyer

First, the upside component of the variance premium is the expensive one: Almeida, Maurer-Muller and Song agree that upside-risk compensation carries the negative same-day return predictability, and Almeida's standardized VRP predicts same-day market return negatively (t -1.7 at 10:00, -2.7 at 11:00; R-squared 0.4-2.6%). A call bought after an up-morning buys the richest part of the surface. At 10:00 the up/down gap is small (0.96 vs 0.85), so this is a direction to check, not a finding. Second, FirmTape's decomposition says the IV path takes back 73-96% of the underlying gain in a straddle; for a single call after an up-drive the usual negative spot-vol relationship would make that worse, and for a put after a down-drive better (my inference [M], not tested in any source). A simulator with IV fixed at k x VIX open cannot show either effect, so it probably flatters calls after up-drives and understates puts after down-drives. That is directly checkable with your existing signals by splitting results into calls and puts.

---

## 4. (b) Real SPY/QQQ execution costs, and which structure suits an intraday directional view

### Measured spread (project quote log, 2026-07-31 to 2026-10-20) [M, project data]

Caveats first: Yahoo quotes trail the share price by 15 minutes (timestamps shifted accordingly), 37 sessions in a mostly quiet tape, polled snapshots, strikes within about $1.40 of spot, and the last 14 minutes of the session are not observed.

| Clock | SPY ATM mid | SPY mean cents | SPY spread % of mid (median) | QQQ ATM mid | QQQ mean cents | QQQ spread % (median) |
|---|---|---|---|---|---|---|
| 10:00-10:30 | $1.23 | 1.08 | 0.85 | $1.65 | 1.35 | 0.75 |
| 12:30-13:30 | $0.90 | 1.03 | 1.14 | $1.07 | 1.30 | 1.06 |
| 14:30-15:00 | $0.57 | 1.05 | 1.77 | $0.75 | 1.22 | 1.60 |
| 15:30-15:46 | $0.48 | 1.05 | 2.11 | $0.56 | 1.53 | 2.25 |

In cents SPY is flat all day; the percentage rises only because premium decays. On wild days (top tercile by range) SPY is unchanged and QQQ widens to 1.49 cents with 7% of quotes at 3 cents or more, concentrated 13:30-15:00 (about 12 days per tercile, so indicative). Spread as a percentage of premium follows 1 cent divided by premium: about 14% below $0.10, 6% at $0.10-0.20, 3.3% at $0.20-0.40, 1.65% at $0.40-0.80, and 0.6% at $1.50-3.00.

This agrees with the regulator and academic picture. The SEC's April 2026 staff data put the median effective spread for ETP options (all expiries) at 1.4% at end-2025 ([SEC roundtable](https://www.sec.gov/files/roundtable-options-market-structure.pdf), A); Almeida and Doshi et al. find 0DTE spreads at their relative minimum 10:00-14:00 and wider at the open and close ([Doshi, Pederzoli, Sert](https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf), B). SPX 0DTE spreads in percent are far wider because SPXW trades in 5-10 cent ticks: Beckmeyer reports retail quoted/effective spreads of 9.6%/4.5% at 3-24 hours to expiry and 19.0%/10.5% in the final hour ([Beckmeyer, Branger, Gayda](https://wp.lancs.ac.uk/fofi2024/files/2024/04/FoFI-2024-146-Leander-Gayda.pdf), B). Do not import SPX cost percentages to SPY/QQQ in either direction.

### Fills: no price improvement to rely on

There are no Rule 605-style statistics for options ([Huang and Schwarz SEC letter, Apr 2026](https://www.sec.gov/comments/4-887/4887-753447-2321514.pdf), A). SEC staff CAT data show marketable single-leg orders sent to the electronic book received 0% price improvement when the NBBO was a nickel wide or tighter, which includes every 1-cent SPY/QQQ market; auction improvement rates were 22-53% ([SEC roundtable](https://www.sec.gov/files/roundtable-options-market-structure.pdf), A, table not isolated to SPY/QQQ). The broker lottery is large: price improvement ranged 7-52% of the spread across six brokers and round-trip cost 0-7% of premium ([Huang, Jorion, Schwarz](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4951825), D). That range is bigger than your whole simulated edge, which is why paper fills cannot settle the question. A 1-cent market has no midpoint on the tick grid, so "fill at mid" is not an option; a SPXW study found a one-second mid limit with a timeout captured about half the half-spread ([Fu et al., SEC DERA](https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf), A), but that needs a wide market to post inside. For a momentum entry, a passive order that fills only when price moves against you also selects against your signal (my inference).

### The fee is the bigger cost

| 1-lot round trip | SPY ($123 premium) | QQQ ($165 premium) |
|---|---|---|
| Spread, crossing both sides | $1.08 (0.9%) | $1.35 (0.8%) |
| IBKR Pro tiered, $1.00 minimum per order, two orders, plus about $0.10 pass-through | about $2.10 (1.7%) | about $2.10 (1.3%) |
| **Total** | **about $3.2 (2.6%)** | **about $3.45 (2.1%)** |
| Same with a roughly $0.05 per contract-side fee (Alpaca-type schedule, secondary) | $1.18 (1.0%) | $1.45 (0.9%) |
| IBKR, 2 contracts per order ($0.65 per contract-side) | about 2.0% | about 1.7% |

The IBKR minimum comes from a secondary summary of the [commissions page](https://www.interactivebrokers.com/commissions) in the notes; your brief says "about $0.65 per contract per side," and if the minimum applies, a 1-lot costs about 54% more than $0.65 suggests. Exchange pass-throughs for removing liquidity on IBKR tiered pricing were not verified; check the first live statement. The +2-3% simulated edge is the same order as 2.1-2.6% friction, so the net depends on the blind trade's mid-price level, which the best evidence puts at about zero to slightly negative (Vilkov straddle mean -0.0011% of spot; Almeida delta-hedged returns significantly negative). A zero-fee broker lowers the fee but routes with more payment-for-order-flow dispersion, so it is not an automatic fix.

### Edge in win-rate terms (my arithmetic, indicative)

With the notes' static values, an ATM single reaches +50% at about +0.146% underlying and -35% at about -0.108%, within 30 minutes. For a driftless random walk, the probability of hitting the target barrier first is 0.108 / (0.108 + 0.146) = 42.5%, giving an expected value of about +1% of premium before theta, trailing logic and costs, so approximately zero by construction [M]. A +2.5% edge is then a lift in target-first probability of about 2.9 points (2.5 / 85), and 2.5% friction is the same 2.9 points. The signal has to lift the hit rate from about 42% to about 45% just to pay for fees and spread, and detecting a 3-point win-rate difference takes about 1,150 trades (binary SD 0.49), consistent with the baseline's 1,000.

### Structure choice

Under any fair-priced zero-drift model every structure's expected P&L before costs is zero, so structure only changes cost per unit of delta, leverage, win-rate/payoff shape and exit geometry [M]. SPY, Bachelier model calibrated to the quote log, 0.5 cent per leg per side, 1-hour hold:

| Structure (SPY, 10:00) | Net delta | Premium | Round-trip cost per delta (cents) | P(profit at expiry) | Share of 10:00 value left after 3h at flat spot |
|---|---|---|---|---|---|
| Long ATM call | 0.50 | $1.20 | 2.0 | 35% | 71% |
| Long 1-strike ITM call | 0.63 | $1.76 | 1.6 | n/a | 82% |
| Long 2-strike OTM call | 0.25 | $0.45 | 4.0 | 21% | 43% |
| Debit call vertical, $1 wide | 0.13 | $0.43 | 15.3 | 44% | 94% |
| Debit call vertical, $5 wide | 0.45 | $1.14 | 4.4 | 35% | 74% |
| Credit put vertical, $5 wide (twin of the $5 debit) | 0.45 | $1.14 credit | 4.4 | 65% | n/a |

What this means for an intraday directional view:

- **The single ATM or one-strike-ITM option is the efficient default.** A narrow vertical pays four crossings for 0.13 delta. The edge scales with delta exposure, not with dollars spent, so the vertical's lower dollar cost is misleading. Complex-order execution might cut vertical costs (up to 83% in a DERA study, [Li, Musto, Pearson](https://www.sec.gov/files/dera-simp-rule-complx-opt-2503.pdf), D), but paper fills cannot show whether your broker achieves that.
- **Do not read probability of profit as quality.** It rises from 35% to 65% across the table while expected value is unchanged; the payoff skew flips.
- **Debit versus credit is a false choice at the same strikes.** The real choices are net delta, width, execution path and, for a short SPY/QQQ leg, American-style early-exercise and assignment risk, which SPXW does not have.
- **One-strike ITM deserves a paired arm.** Lower cost per delta (1.6 vs 2.0 cents), slower decay, but lower gamma, so it needs a bigger underlying move for the same percentage gain. This is model-based; I found no ETF returns-by-moneyness study.
- **Avoid cheap OTM.** The penny tick turns a $0.10 option into a 10-14% spread, and SPX evidence says OTM 0DTE calls have the most negative average returns (Almeida).
- **Percentage exits mean different underlying moves per structure.** A -35% stop on an ATM single is about 1 sigma of a 30-minute move, so it triggers on noise; a $1 debit vertical needs about 1.4x the move for the same +50%. Compare structures at equal underlying moves, not equal percentages.

---

## 5. (c) What can be replicated, and with what data

**Replicable now with your assets** (SPY/QQQ 1-minute bars, Black-Scholes simulator):
- Vilkov's realized-moment features (realized variance, semivariances, skew) and his protocol: expanding or rolling 252-day logistic classifier, hard sign mapping, gross and net reported separately, calibration slope and Brier score. His OOS table shows why to keep that discipline: 12 of 14 strategy-protocol rows have negative net Sharpe, the best gross Sharpe (1.09, iron fly rolling) is net -3.80, calibration slopes are mostly 0.1-0.3, and expanding/rolling versions flip sign on some strategies, a sign of noise [B].
- A sanity test of your simulator: his mid-price means are near zero and the mid-price Sharpes of +0.5 to +1.06 vanish at the touch. If your Black-Scholes simulator shows a stable net edge for such structures, suspect the simulator.
- His correction as a lesson: rebuild the target whenever the cost model changes, and add a unit test on cost magnitudes (the bug charged the half-spread at 1/100 of its size).

**Needs real quotes** (cheapest routes, prices as read from vendor pages, verify before buying):

| Source | Content and history | Price | Caution |
|---|---|---|---|
| [ThetaData](https://www.thetadata.net/pricing) Value | 1-minute option NBBO; docs say from 2020-01-01, pricing page says 6 years | $40/month (commercial use quoted far higher) | Docs and pricing page disagree; confirm SPY/QQQ 0DTE coverage first |
| [Massive (ex-Polygon)](https://massive.com/pricing?product=options) Advanced | Minute aggregates, trades and quotes, 5+ years | $199/month | Quotes only on this tier |
| [Databento](https://databento.com/pricing) OPRA CBBO-1m | NBBO plus last sale, 1-minute, back to 2013-04-01 | Pay per GB; $125 credit | Needs symbol filtering; cost via their estimator |
| [Alpaca](https://docs.alpaca.markets/docs/historical-option-data) free | Indicative feed, history only since Feb 2024 | Free | **Derived, not OPRA NBBO and 15-minute delayed per the baseline; cannot measure spreads** |
| Kaggle | 16:00 EOD only | Free | Cannot test a 10:00 0DTE entry |

Two consequences. Your pending Alpaca keys will not answer the cost question, because the free feed is indicative; use it, if at all, for mid-price checks. And daily same-day expiry for SPY/QQQ (Tue/Thu) was scheduled to begin in Nov 2022 per a [Cboe product update](https://cdn.cboe.com/resources/product_update/2022/Update-Cboe-Options-to-List-SPY-and-QQQ-Tuesday-and-Thursday-Expiring-Weekly-Options.pdf) (first-trade dates not verified). A 2016-2026 "0DTE" simulation therefore assumes same-day options on days when they did not exist, and Almeida and O'Donovan both find the pricing regime changed around the 2022 daily-expiry launches. Your "fading since 2025" may be partly a regime or concentration effect; the ten-year t of about 3 is likely dominated by years that do not describe today's market (my inference [M]).

**The unmeasured error:** no open study reports the dollar error of Black-Scholes with VIX-derived IV against real 0DTE quotes. The qualitative picture is consistent: VIX1D carries a built-in intraday drift ([Albers and Kestner](https://www.sciencedirect.com/science/article/pii/S1544612324002162), D), FOMC-day implied uncertainty rises monotonically into 14:00 (Song), and a wrong short-end vol input produced a median 47% gamma error over 49 SPX sessions (Chilingarian, D). Only your own comparison against real quotes yields a SPY/QQQ number.

---

## 6. What to change in the tool, in priority order

Ordering reflects value per unit of effort and how decisive the evidence is. "Strength" grades the evidence behind the change, not the expected payoff.

### Do first (code only, days)

**1. Add a fee and fill model to simulator and paper P&L.** Strength: strong for fees (broker schedule), moderate for spreads (37 sessions, delayed quotes, quiet tape).
- Buy at ask, sell at bid. No midpoint fills for 1-cent markets; price improvement 0% default, 25% as a sensitivity only.
- Half-spread per side: SPY 0.54 cent; QQQ 0.68 cent at 10:00-10:30 (use 0.75 on high-range days); double both on stop exits as a sensitivity.
- Fees: model the IBKR order minimum ($1.00 per order for 1-lot, so $2.00 round trip plus about $0.05 per contract-side pass-through) and report P&L gross and net of fees. Verify the minimum and any exchange removal fees against a live statement.
- Premium floor: refuse entries below $0.30 mid for any non-morning-family test (spread above 3%).
- Add a unit test on cost magnitudes, as Vilkov's repo did after its half-spread bug.

**2. Restate what the 40-day rule can show.** Strength: strong (arithmetic).
- At SD 40% of premium, 40 independent days give SE 6.3%; passing t >= 2 needs a +12.7% mean, five times the simulated edge. With a true +2.5% edge the pass probability is about 5.4%; with none, about 2.3% [M]. The rule's pass is nearly uninformative; its failure is only informative if the result is clearly negative on real fills.
- Count independent days, not trades: the three morning strategies often fire the same direction on the same day, so 120 trades are about 40 observations.
- Do not edit the pre-registered rule silently. Add a dated note that it is a loss detector and a cost-measurement window, and report the SE and power beside every result.

**3. Log the fields that decide interpretation.** Strength: moderate (vendor mechanism; cheap).
- Per trade: entry IV and 10:00 implied move (ATM straddle), morning realized move divided by implied move, underlying move to exit, and option P&L decomposed into underlying plus decay, IV path, spread and fees (FirmTape's design).
- Report calls and puts separately and FOMC days separately. This checks the constant-IV artifact in section 3 at no cost in looks (descriptive only).
- On every real fill, log fill price against NBBO mid and quoted spread at submission. This is the only way to measure your broker's effective spread; the 0-7% broker range exceeds your edge.

### Do next (about $40-$199 and a week)

**4. Replay the morning family on real SPY/QQQ NBBO.** Strength: this is the highest-value test available; the qualitative case that Black-Scholes with k x VIX misprices 0DTE is established, the magnitude is unmeasured.
- Buy one month of ThetaData Value (or Massive Advanced) and pull 1-minute quotes for ATM plus about five strikes each side, SPY and QQQ, for dates with same-day expiry. First confirm coverage and licence terms; check the docs/pricing discrepancy.
- Pre-register the analysis before pulling: (i) simulated versus real mid at entry and exit by time of day, moneyness and FOMC flag; (ii) the morning family's net P&L with ask-in/bid-out fills and the section-1 fee model, post-Nov-2022 (about 980 trading days) and pre-Nov-2022 reported separately.
- Power: with SD 40%, 980 days give SE about 1.3%; a +2.5% edge gives t about 1.95, roughly even odds of passing (my arithmetic). Even a negative result is informative about the absolute level; a positive one is still provisional because NBBO bars are not fills.
- This replaces waiting for 1,000 paper trades with a sample you can have in weeks.

**5. Add a one-strike-ITM single as a paired shadow arm.** Strength: weak to moderate (model-based, [M]); no ETF empirical test exists.
- Same signals and days as the ATM trade; strike is the nearest ITM; record the underlying move at exit, because percentage exits imply different underlying moves for ITM and ATM.
- Interpret as cost per unit of delta and exit geometry, not as an expectancy difference: before costs, expected P&L is equal under a martingale.

### Optional (count each against the test budget)

**6. At most one structure arm.** Strength: weak.
- If you test a vertical, test one $5-wide debit call/put vertical, not $1-2 wide ones (cost per delta 4.4 vs 15.3 cents). Expect a small difference and do not expect paper fills to show complex-order savings.
- Drop the "credit spread in the signal direction" arm unless you want the premium-selling hypothesis specifically, in which case use a short strike 1-2 strikes OTM (the only variant with a literature reason for positive gross: selling slightly OTM is profitable in up to about 75% of median observations while means are mostly n.s.) with a random-direction credit-spread null, judged on worst day and drawdown. Account for American-style assignment on SPY/QQQ short legs.

**7. Entry time as a descriptive replay only (10:00, 10:30, 11:00).** Strength: weak. VRP is lowest 10:30-11:00, so the data cannot discriminate within the window; run it inside the real-quote replay rather than as live arms.

**Test budget.** Confirmatory tests: (A) morning family net on real-quote replay, (B) ITM versus ATM paired, (C) one structure arm. Everything else is descriptive. Three tests put a Bonferroni-style threshold at t about 2.4 rather than 2 (my arithmetic).

### Stop doing

| Stop | Why | Strength |
|---|---|---|
| Treating the simulation's absolute net level as informative, or the ten-year t as describing today's market | Constant-IV model; same-day options did not exist daily before Nov 2022; mid-price results flip at the touch in every comparable study | Strong |
| Reading mid-price Sharpes, win rates or Option Alpha/vendor backtests as evidence | Vilkov's +1.06 became -0.61; Kam's 3,909 vendor backtests were 99.9% profitable, a selection effect; Tachyon, QuantedOptions and thunderscarf fill at mid or omit slippage | Strong |
| Using the 40-day rule as confirmation of profitability | About 5% power at the simulated edge | Strong |
| Adding more filters or tuning (entry minute, target, stop, strike, calendar, VIX or gamma splits) | Each is a draw; the conditional evidence is contradictory (VIX), non-replicating (gamma, realized-vol regime) or untested (gap, weekday) | Strong |
| Treating Alpaca's free indicative feed as a spread measurement | Derived quotes, not NBBO | Moderate |
| Quoting Vilkov's SSRN abstract or `docs/` numbers | Pre-correction (e.g. put ratio net 0.93, top-3 basket 0.82); the corrected figures are negative | Strong |
| LLM gating, commercial GEX signals, closing-half-hour option plays | No evidence of benefit; the dealer-gamma effect fails a holdout; spreads widen into the close | Moderate |
| Narrow ($1-2) verticals and cheap OTM | 4-8x cost per delta; spread 10-14% below $0.10 | Moderate (model plus tick arithmetic) |

### What remains unknowable without real option history

- The absolute net level of the morning family on SPY/QQQ. Only real quotes (step 4) can answer this, and even then fills, not quotes, decide it.
- Your broker's effective spread and complex-order handling. They vary by up to the whole quoted spread across brokers, and paper accounts do not reproduce them.
- Stop-fill slippage in fast markets. The Yahoo log is delayed, so it cannot see the moments that matter; only real-time recorded quotes and real fills can. Practitioner anecdote ([Early Retirement Now](https://earlyretirementnow.com/2026/01/30/options-trading-series-part-14-year-2025-review/)) says stop orders sent to market produced "crazy fills" on a +10% day.
- The last 14 minutes, pin and assignment behavior for short SPY/QQQ legs, and FOMC or tail days (the log has one quiet Q3-Q4 regime).
- The sign of a 10:00 buyer's P&L through an FOMC ramp: sources disagree and the sample is about 8 days a year.
- Whether calls after up-drives and puts after down-drives behave differently in real IV. My inference; no source tests it.
- Anything about QQQ/NDX 0DTE returns, which no source covers.

---

## 7. Conclusion and limits

The deep dive changes the question again. The baseline said the issue is not signal quality but whether the trade survives costs. For SPY/QQQ at 10:00 the quoted spread is already small, so the trade survives or fails on three things the current tool does not yet measure: the broker fee, the real fill, and the IV path that offsets the underlying move. At current estimates all three together are about the size of the signal. That is not a verdict that the family loses; it is a statement that the plausible net range is roughly -3% to +1% of premium and that the question will be settled by a real-quote replay plus measured fills, not by tuning, structure switching or 40 paper days.

Limits of this report. It is a synthesis of notes, not a fresh literature search; I did not re-open primary sources. Several key papers were abstract-only (Maurer-Muller, Dorion et al., Singh, Bozovic, O'Donovan, Johannes-Kaeck-Seeger, Chong, Wilkens), and the Huang-Schwarz price-improvement table was garbled in extraction. FirmTape and FlashAlpha are vendors with short samples (61 sessions, 35 FOMC days, 193 Wednesdays) and single regimes. All hard numbers outside the project's own log are SPX, apart from the FlashAlpha SPY study. The project log is 37 sessions of delayed Yahoo quotes. The model figures in section 4 are my Bachelier calculations on stated assumptions, not evidence. Vendor prices and the IBKR minimum come from page reads and secondary summaries and should be verified before spending money or changing the fee model.
