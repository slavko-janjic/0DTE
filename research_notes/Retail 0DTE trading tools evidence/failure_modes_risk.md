# Failure modes and risk management for automated 0DTE trading (SPX/SPY/QQQ, 2022-2026)

Research date: 2026-10-20. Most pages were read through a fetch tool that returns summaries, so numbers are as reported by that tool. Paywalled or blocked pages (SSRN, IBKR, CXO full text) were not read directly. Vendor and marketing sources are flagged inline.

## 1. Failure modes and their measured impact

### Takeaway
The best primary evidence says unconditional, mid-priced 0DTE rules show a small variance-risk premium that disappears after realistic costs. Retail 0DTE losses are dominated by transaction costs. A 2026 correction to the main academic backtest (Vilkov) flipped its net-of-cost result from mildly positive to negative. Tail days (Aug 5 2024, Apr 9 2025) are what break short-premium systems.

### Cited Findings

**Costs, spreads and slippage**
- Beckmeyer, Branger and Gayda (Cboe SPX data, Jan 2021 to Feb 2023): retail lost more than $70M. Average daily loss was $184k over the whole sample and $358k after daily expirations began on 2022-05-16. About 60% of the daily loss was transaction costs. About 60% of losses came from 0DTE puts. — [Wealth Management summary](https://www.wealthmanagement.com/equities/retail-investors-are-love-same-day-options); [CXO Advisory](https://cxoadvisory.com/individual-investing/retail-0dte-option-trader-performance) (full paper: SSRN 4404704, not accessible, 403)
- Same paper: long 0DTE positions lost 53% of value on average (61% since May 2022). Short positions returned +48% (+56% since May 2022). This describes retail flow, not a tradable edge. — [Wealth Management](https://www.wealthmanagement.com/equities/retail-investors-are-love-same-day-options)
- Same paper: effective spreads were 6.1% (calls) and 4.9% (puts) for retail trades versus 13.1% and 10.1% for non-retail trades. Retail kept trading with under an hour to expiry although spreads widen near expiry. — [Wealth Management](https://www.wealthmanagement.com/equities/retail-investors-are-love-same-day-options)
- The only SPY/QQQ spread data I found: Schaeffer's (Rocky White), a single day (2023-09-13), end-of-day quotes about 5 minutes before the close. The nearest-$1 SPY 0DTE option had an average spread of 5.8%, versus about 1% for other expirations. One ATM example was a $0.14/$0.15 market, which is 6.7% of premium. QQQ showed the same pattern, with no number. White cautions that the 0DTE figure is inflated by the low premium late in the day, and he had no intraday data. — [Schaeffer's](https://www.schaeffersresearch.com/content/bgs/2023/09/22/0dte-options-are-higher-spreads-imminent)
- Dollar spreads (vendor, no sample stated): ATM SPY 0DTE spreads typically $0.02-0.05, versus $0.10-0.30 on SPX. — [FlashAlpha](https://flashalpha.com/articles/0dte-spy-complete-intraday-playbook-same-day-options)
- Vendor claim, unverified: Lambda Finance says round-trip ATM 0DTE spreads on SPX are about 1-3 bps of the underlying (versus 3-8 bps for weeklies). It publishes no code, data source or trade log. Its backtest uses mid fills plus 1 tick slippage and no commissions. — [Lambda Finance](https://www.lambdafin.com/articles/0dte-vs-weekly-options-compared)
- Saxo says liquid ETF options (SPY, QQQ) can have 1-2 cent spreads and recommends round strikes (ending in 0 or 5). It gives no numeric slippage tolerance. — [Saxo](https://home.saxo/content/articles/options/trading-0dtes---get-your-feet-wet---without-drowning---part-2-07092023)
- ORATS vendor backtest of QQQ 0DTE iron condors (20-delta shorts, 5-delta wings, entry 09:45, since Oct 2020): 1,156 trades, 81% winners, Sharpe 0.64. Slippage is a percentage of bid-ask width (the percentage is not stated) plus $0.65 per contract commission. This is marketing material with no independent verification. The page's own example shows a stop set at a 200% loss ending with a loss nearly double that. — [ORATS](https://orats.com/intraday-backtester)

**Look-ahead, overfitting and implementation errors**
- Vilkov, "0DTE Trading Rules" (SSRN 4641356), tests static daily rules held to expiry on SPXW options, Sept 2016 to Jan 2026. The author's own repository documents a units bug: the bid-ask half-spread was charged at about 0.022 bp instead of about 2.2 bp. After the fix, unconditional net Sharpe ratios on the 2024-05-01 panel are (mid, then net): put ratio spread +1.06 / -0.61; risk reversal +0.65 / +0.10; bear put spread +0.48 / -0.73; strangle/straddle -0.27 / -0.97; iron butterfly/condor -0.56 / -2.67. The README says "no strategy or basket retains a positive net Sharpe ratio." Conditional (model-based) results also flipped sign (put-ratio net Sharpe +0.93 to -0.75). Cost assumptions: 0.5 bp fixed fee plus an average half-spread of about 2.2 bp, tied in the README to a mean dollar spread of about $1.33 for SPXW 0DTE (whether that is a half or full spread is ambiguous). The paper PDF is still being revised. — [KNOWN-ISSUES.md](https://github.com/vilkovgr/0dte-strategies/blob/main/KNOWN-ISSUES.md); [README](https://github.com/vilkovgr/0dte-strategies)
- Earlier summary of the same paper: 0DTE options contain a variance risk premium; individual option returns are extremely skewed; most strategy means are not statistically significant; realized skewness of the index drives much of the P&L. A secondary write-up says only risk reversals showed a slightly positive mean (about 0.01% of index value). Disregard the earlier "net positive" tone given the correction above. — [Hull Tactical summary](https://hulltactical.com/2026/07/08/0dte-options-tiny-edge-huge-distribution/); [Breaking NASDAQ, secondary](https://breakingnasdaq.beehiiv.com/p/the-0dte-illusion)
- Vilkov methodology (Nov 2023 version): 30-minute 0DTE prices interpolated to moneyness 0.98-1.02, entry at 10:00 (with 13:00 and 15:00 variants), hold to 16:00; straddles, strangles, spreads, ratio spreads, risk reversals. — [CXO Advisory](https://cxoadvisory.com/equity-options/0dte-strategy-outcomes)
- Multiple testing: Harvey and Liu convert Sharpe ratios to t-ratios, adjust p-values for the number of tests tried, and convert back. The haircut is nonlinear: marginal Sharpe ratios are penalized heavily and high ones only moderately. The common "cut Sharpe by 50%" rule is only a rough convention. — [Harvey and Liu, Backtesting (Duke PDF)](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF)
- Bailey, Borwein, Lopez de Prado and Zhu: the Probability of Backtest Overfitting is estimated via combinatorially symmetric cross-validation, and a minimum backtest length depends on the number of configurations tried. The paper argues hold-out samples are unreliable guards in investment backtests. I did not read the formulas. — [SSRN 2326253](https://papers.ssrn.com/abstract=2326253)
- Numerix white paper warns that backtesting challenges (data quality, overfitting, cost accounting) are amplified at ultrashort timeframes. — [Numerix](https://www.numerix.com/node/1136)
- Vendor claim: simulated iron-condor results without historical IV data can overstate profit by 30-50%. Treat as directional only. — [Backtrex](https://backtrex.com/en/blog/backtesting-options-strategies-guide)

**Tail events**
- Aug 5 2024: BIS says the VIX had its largest one-day spike on record, exceeding 2008 and Mar 2020. Widening bid-ask spreads in options, particularly puts, fed into the VIX because it is quote-based. — [BIS Bulletin 95](https://www.bis.org/publ/bisbull95.htm)
- Aug 5 2024 detail (ORATS): pre-market VIX hit about 65 (thin market), VIX about 40 five minutes after the open, 0DTE IV about 80%, deep OTM put IV went from 36% to 90%, put-call skew slope peaked around 9% (versus a "new normal" of about 5%). End-of-day 0DTE volume was 496k calls and 616k puts. — [ORATS](https://orats.com/blog/0dte-and-the-august-fifth-correction)
- Apr 2025: the worst session for SPY 0DTE straddle sellers in a 193-Wednesday sample (2022-2026) was Apr 9 2025. SPY closed +8.72% against a 10:00 implied move of 3.51%, about 2.5x. — [FlashAlpha study](https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study) (vendor; quote mids, no costs, Wednesdays only)
- Practitioner account (Early Retirement Now, personal results): 2025 0DTE puts captured 62.2% of premium and 0DTE calls 53.1%. April's 0DTE/1DTE premium was "largely given back." Stops triggered on Apr 9 when open-to-high was +10.39%. He moved to stop-limit orders after "crazy fills" on 2025-10-22 when stop orders executed as market orders. Typical daily size was 6-8 contracts for 0DTE puts and 20-24 for 0DTE calls. — [ERN 2025 review](https://earlyretirementnow.com/2026/01/30/options-trading-series-part-14-year-2025-review/)
- Cboe: retail share of SPX 0DTE fell from 57% to 47% in the early-April 2025 spike and recovered to about 60% afterwards. — [Cboe](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact); consistent with [Northern Trust](https://www.northerntrust.com/japan/insights-research/asset-servicing/a-suite/insights-hub/options-quarterly-commentary-q2-2025)
- 2026: SpotGamma (vendor, subscription newsletter) says SPX fell about 3% over three sessions to 6,798 around Feb 5 2026 and argues 0DTE gamma support disappeared overnight. A separate 2026 analysis of Cboe records (seen only in search results) says dealers were long about $2B of gamma at 3pm when the selloff started and turned short only after the index stabilized, so the causal story is contested. — [SpotGamma](https://spotgamma.substack.com/p/the-markets-0dte-underbelly-is-exposed)
- A tastylive segment dated 2026-03-16 claims 0DTE iron condors held up with VIX near 30 because IV stayed above realized volatility. It is commentary, not a formal backtest. — [tastylive](https://www.tastylive.com/shows/market-measures/episodes/weekly-0-dte-breakdown-when-30-vix-still-wasnt-enough-to-break-0-dte-03-16-2026)

**Regime change / market structure**
- Cboe: SPX 0DTE volume is up more than 5x in three years to nearly 2M contracts a day (May 2025). Retail is estimated at 50-60% of SPX 0DTE. Over 95% of 0DTE trades are limited-risk (long options or spreads); naked shorts are about 4%. Net market-maker gamma hedging is at most about 0.2% of SPX daily liquidity. — [Cboe](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact)
- Volume share: 0DTE is about 57% of SPX volume in Q3 2025 (Cboe, via search summary) and about 63% in Feb 2026 (SpotGamma, vendor). A record 3.1M SPX 0DTE contracts a day was reported for June 2026 (SpotGamma, vendor). — [SpotGamma](https://spotgamma.com/0dte/)
- Eraker/Vilkov abstract: high open-interest 0DTE gamma does not propagate past volatility, and intraday 0DTE volume shocks do not amplify recent index returns. — [Oxford seminar listing](https://www.maths.ox.ac.uk/node/67396) (abstract only)
- FlashAlpha 193 SPY Wednesdays: close inside the implied 1-sigma band 71.0% (benchmark about 68.3%), stable at 69-71% for 2022-2025, median realized/implied move 0.589. Median implied move 0.72% of spot, median absolute realized move 0.37%. The premium is "real but modest," quoted at mid with no costs. — [FlashAlpha](https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study)

### Inferences
- A realized-vs-implied move gap of about 13% (FlashAlpha) is the same order as 5-6% effective retail spreads per leg and Vilkov's 2.2 bp half-spread plus fees. Spread cost is a first-order effect on 0DTE expectancy, not a second-order one.
- Vilkov's unit-scale error is itself a lesson: a net-of-cost result changed sign through a bug. Unit tests on cost magnitudes (the repo added a guard, `assert_percent_of_spot_scale()`) are cheap and worth copying.
- Any comparison of many entry times, deltas, wings, stops and filters on a few hundred days is the Harvey-Liu and Bailey multiple-testing problem. Log the number of variants tried.
- Stress-day fills (Aug 5, Apr 9) are where mid-price backtests are least accurate: spreads blow out exactly when stops trigger.

### Gaps
- No primary source quantifying SPX vs SPY vs QQQ 0DTE spreads as a percentage of premium by time of day. Only a one-day Schaeffer's sample (SPY, 5 min before close), Beckmeyer (SPX, all-trades effective spreads), and vendor figures. Measure from the user's own recorded quotes.
- Could not read the full Beckmeyer et al. (SSRN 4404704) or Vilkov (SSRN 4641356) PDFs (403 / under revision). Spread vs time-to-expiry curves and tail statistics are unavailable.
- No reliable aggregate P&L figure for 0DTE sellers on Aug 5 2024 or in Apr 2025. Only anecdote (ERN) and market-structure data.
- 2026 events: only the Feb 2026 selloff narrative (vendor) and a single-trader blog postmortem for Oct 19 2026; no independent loss data.

## 2. Risk sizing practices

### Takeaway
Practitioner guidance is mostly qualitative: risk about 1% of the account per trade (up to 5% for small accounts), prefer defined risk, use stops, cap daily loss. I found no rigorous source tying Kelly sizing to 0DTE. One practitioner who sells undefined-risk premium sizes to survive a day like Apr 9 2025.

### Cited Findings
- Saxo: size to a predefined maximum loss; about 1% of the account per trade ($1,000 on $100k), up to 5% for small accounts; start with one contract; keep margin utilization at or below 50% when selling premium; stop short legs at 3x the credit received; defined-risk butterflies may skip stops. Its worked example has arithmetic inconsistencies (3 contracts at $1,050 against a $1,000 cap). — [Saxo](https://home.saxo/content/articles/options/trading-0dtes---get-your-feet-wet---without-drowning---part-2-07092023)
- General sizing sources (low quality): retail range 1-3% per trade; full Kelly is aggressive, with half or quarter Kelly used in practice; Kelly inputs (win rate, payoff) are estimated from backtests and so unreliable. I found no 0DTE-specific daily loss limit number. — [TradersPost](https://blog.traderspost.io/article/position-sizing-algorithms)
- Cboe: over 95% of SPX 0DTE flow is defined-risk, naked shorts about 4%, and retail participation falls in stress. — [Cboe](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact)
- ERN (personal results, undefined-risk, stop-based): "don't scale too aggressively; size so you can survive a temporary drop like April 2025"; accept small stop-outs to avoid a catastrophic loss; use stop-limit rather than stop-market orders, with the limit 1-3 ticks beyond the stop. — [ERN](https://earlyretirementnow.com/2026/01/30/options-trading-series-part-14-year-2025-review/)
- Tail magnitude for sizing: 2.5x the implied move on Apr 9 2025 (SPY +8.72% close vs 3.51% implied). — [FlashAlpha](https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study)

### Inferences
- Sizing from a gap-through scenario (e.g. 2.5x the implied move with wide-spread fills) is more robust than sizing from average historical loss. For defined-risk spreads the per-trade max loss is bounded, so a 1% rule bounds the daily loss if trade count is capped; for undefined risk it does not.
- If net expectancy is indistinguishable from zero after costs (Section 1), Kelly's optimal fraction is about zero. Kelly is only meaningful after a positive net expectancy is demonstrated out of sample.
- A hard daily-loss cap plus a hard cap on trades per day is simple to implement in an autopilot.

### Gaps
- No research-grade recommendation for 0DTE Kelly fractions, daily loss limits or drawdown limits. tastylive-specific sizing rules were not found.
- No evidence comparing defined-risk and undefined-risk 0DTE drawdowns beyond anecdotes.

## 3. Filters claimed to improve results

### Takeaway
Nearly all filter evidence is vendor or practitioner and small-sample. Vilkov flags overnight gap, realized-volatility regime, macro-event days and large morning moves as candidate conditioning variables, but model-based conditional results turned negative after the cost fix. No filter has solid, independent, out-of-sample support in what I found.

### Cited Findings
- Vilkov (via Hull Tactical): conditional rules based on overnight gaps, low realized-volatility regimes, macro-event days and large morning moves are "the next research step," not tested in that article. — [Hull Tactical](https://hulltactical.com/2026/07/08/0dte-options-tiny-edge-huge-distribution/); conditional results flipped negative per [KNOWN-ISSUES](https://github.com/vilkovgr/0dte-strategies/blob/main/KNOWN-ISSUES.md)
- IV vs RV: realized moves ran about 13% below implied in 193 SPY Wednesdays; the hit rate was stable at 69-71% for 2022-2025 and 84.6% in 13 sessions of Q1 2026. Fed-day straddles priced "roughly double the neighboring weeks" (no separate hit rate). — [FlashAlpha](https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study)
- FOMC: one options-trader analysis found SPX averaged about a 2% move on FOMC days vs about 1.25% otherwise (small sample, about 8 meetings a year). A tastylive segment is reported (search summary) to say a 1:05pm post-announcement entry went 23 for 23 with an average P&L of $184, and to recommend skipping FOMC or waiting for the vol crush. 23 trades is too small to conclude anything. — [tastylive](https://www.tastylive.com/shows/market-measures/episodes/weekly-0-dte-breakdown-trading-into-fomc-post-fomc-achieves-100-win-rate-in-the-past-three-years-12-15-2025) (episode page could not be verified directly)
- Option Alpha's backtester supports entry filters such as no FOMC, no Friday, reward/risk above 400% and VIX below 20, but the page publishes no results for them. Its own example shows a $15,000 P/L difference between a 10:30 and a 14:30 SPX condor entry (company example, not independent). — [Option Alpha](https://optionalpha.com/blog/enhanced-backtesting-logs-show-why-trades-were-filtered); [Option Alpha video](https://optionalpha.com/videos/morning-vs-afternoon-0dte-backtested-options-strategies)
- Day of week: a tastylive 2-year backtest of 0DTE iron flies (Aug 2025) reports Thursday as the worst day. $20-wing flies with a 10% profit target won 78%; with a 20% target they won 61%. No cost detail. — [tastylive](https://www.tastylive.com/shows/market-measures/episodes/zero-dte-winners-and-losers-for-iron-flies-08-07-2025)
- VIX level: tastylive claims condors survive VIX near 30 (see Section 1); ORATS shows no VIX filter. Neither is a controlled test. — [ORATS](https://orats.com/intraday-backtester)
- GEX / dealer gamma: every source found is a vendor (SpotGamma, FlashAlpha, OptionsAnalysisSuite) stating the mechanism as fact. One vendor concedes GEX is a model output, not a measured quantity, and depends on the assumption that customers are net buyers. Cboe estimates net market-maker gamma hedging at at most about 0.2% of SPX daily liquidity, and the Eraker/Vilkov abstract finds no propagation of 0DTE gamma into volatility. — [OptionsAnalysisSuite](https://www.optionsanalysissuite.com/documentation/dealer-gamma); [Cboe](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact)
- Time of day: Saxo says entries at the open (full-day move), around lunch (usually calmer) or in the last hours (faster theta) are all used and that the final minutes can be erratic; Beckmeyer finds spreads widen near expiry. No controlled comparison with costs. — [Saxo](https://home.saxo/content/articles/options/trading-0dtes---get-your-feet-wet---without-drowning---part-2-07092023)

### Inferences
- Each filter shrinks the sample, so a filter's effect is even harder to detect (Harvey-Liu). Pre-register filters and test out of sample.
- If event-day IV is priced about 2x, skipping or downsizing event days is more likely to avoid fat tails than to raise expectancy.
- A stable 69-71% selling hit rate says nothing about expectancy once rare large losses and costs are included.

### Gaps
- No peer-reviewed or independent test of VIX term-structure filters, IV/RV filters with costs, GEX regime, opening-range filters, trend vs range day classification, or gap-size filters.
- Day-of-week and time-of-day: only vendor/practitioner anecdotes.
- The tastylive FOMC 23/23 claim is from a search summary only.

## 4. Structural alternatives for a directional view

### Takeaway
The only quantitative comparison I found is a single unverified vendor backtest: long 0DTE options have clearly negative expectancy and credit spreads are marginally positive before commissions. I found no published study directly comparing debit spreads, long singles and credit spreads on 0DTE.

### Cited Findings
- Beckmeyer et al.: long retail 0DTE positions lost 53% (61% since May 2022) of value on average, versus short positions +48% (+56%). — [Wealth Management](https://www.wealthmanagement.com/equities/retail-investors-are-love-same-day-options)
- Lambda Finance (vendor, unverifiable), SPX Jan 2023 to Mar 2026, equal risk per trade, mid plus 1 tick, no commissions: long ATM call win rate ~28%, average -0.18R; long ATM straddle ~22%, -0.21R; short 10-delta credit spread ~78%, +0.06R; short ATM iron condor ~62%, +0.04R. Weekly versions did better for long options. — [Lambda Finance](https://www.lambdafin.com/articles/0dte-vs-weekly-options-compared)
- EarningsWatcher reports (no numbers) that multi-year SPY 0DTE call-buying backtests lose at ATM, 1% OTM and 2% OTM strikes. — [EarningsWatcher](https://earnings-watcher.com/wiki/0dte-spy-calls-losing-game)
- Vilkov corrected results: bear put spread mid Sharpe +0.48 becomes -0.73 net; risk reversal +0.65 becomes +0.10 net. — [KNOWN-ISSUES](https://github.com/vilkovgr/0dte-strategies/blob/main/KNOWN-ISSUES.md)
- Option Alpha uses spreads rather than single SPX contracts to limit cost per trade; TradeStation notes credit strategies capture positive theta. — [Option Alpha](https://optionalpha.com/blog/0dte-options); [TradeStation](https://www.tradestation.com/wp-content/uploads/0DTE-Zero-Days-To-Expiration-Options.pdf)

### Inferences
- A debit spread pays two half-spreads at entry; near-the-money legs carry a smaller spread share of premium than a single far-OTM option. The ratio of round-trip spread cost to expected move is the number to track per structure.
- Win rates mislead: a capped debit spread has a different payoff shape. Compare expectancy per dollar risked and after-cost Sharpe.
- A credit spread in the signal direction earns the (small) variance premium plus a directional edge only if the signal is real; test the signal and the premium capture separately.

### Gaps
- No independent head-to-head study of debit vs long vs credit structures on 0DTE with real bid/ask. It must be measured with the user's own recorded chains.

## 5. Retail paper trading and free data feeds versus real fills

### Takeaway
Retail paper trading and free feeds are optimistic or lagged: thinkorswim paperMoney fills at mid and ignores size, IBKR paper gets 15-minute delayed US options data unless tied to a funded live account's subscription, and Alpaca's free options feed is indicative rather than real OPRA. Alpaca's paper fills at ask/bid are conservative, but the quote source matters.

### Cited Findings
- thinkorswim paperMoney: multi-leg option fills are typically at the midpoint of each leg's bid/ask, which "may range from realistic to extremely unrealistic"; order size is ignored; early exercise is never simulated. — [Investing Daily quoting TOS documentation](https://investingdaily.com/paper-trading) (I did not find the wording in a Schwab document)
- Alpaca paper: simulated fills use NBBO-style quotes, buy at the ask and sell at the bid, with no price improvement. — [Alpaca forum](https://forum.alpaca.markets/t/paper-trading-options-pricing/17795)
- Alpaca options data: the OPRA feed requires the Algo Trader Plus subscription. The free indicative feed has derived (not real OPRA) quotes and trades delayed by 15 minutes, and Alpaca staff describe it as for debugging, not strategy validation. A user reported a frozen chain in paper. — [Alpaca forum, indicative feed](https://forum.alpaca.markets/t/what-is-the-indicative-pricing-feed-for-options/14595); [Alpaca docs](https://alpaca.markets/docs/options-trading)
- IBKR: unsubscribed US options in paper accounts are delayed 15 minutes, and live data in paper requires sharing a funded live account's subscription (from a search summary; the IBKR page returned 403). — [IBKR](https://www.interactivebrokers.com/en/trading/papertrader-delayed-data.php)
- TradersPost: paper accounts default to midpoint fills; switching to ask-for-buys and bid-for-sells removes the midpoint credit, and the gap grows with spread width. — [TradersPost](https://blog.traderspost.io/article/paper-trading-vs-live-results)
- Live reference points: ERN's experience of poor fills on market-order stops on fast days; ORATS's stop-out example ending near double the stop level. — [ERN](https://earlyretirementnow.com/2026/01/30/options-trading-series-part-14-year-2025-review/); [ORATS](https://orats.com/intraday-backtester)

### Inferences
- With 5-6% effective spreads (Beckmeyer) and fast-moving 0DTE quotes, a 15-minute delay makes any intraday signal untestable. A delayed-quote paper run is not a test of the strategy.
- Recording real-time bid/ask snapshots (even once a minute) from the live feed gives the data to measure spread and slippage empirically. The fill model should use natural (ask for buys, bid for sells) or mid minus a fraction of the spread, never raw mid, and should apply a wider assumption for stops on stress days.

### Gaps
- No documented measurement of how paper fills deviate from live fills on 0DTE SPX/SPY/QQQ options.
- Nothing found on Tradier, Schwab API or tastytrade sandbox behaviour.
- Could not confirm how IBKR prices simulated option fills against delayed quotes.