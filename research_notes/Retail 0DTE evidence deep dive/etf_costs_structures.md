# SPY / QQQ 0DTE execution economics and option-structure choice (as of 2026-10-20)

Scope: SPY and QQQ same-day options (SPX only as contrast), daily expirations era (SPY/QQQ Tue/Thu weeklies were scheduled to list from 14/16 Nov 2022 per a Cboe product update; first-trade dates not verified, so "daily 0DTE" is Nov 2022 onward, Mon/Wed/Fri before that).

## 0. How to read this note

Source tiers used below:
- **[P]** primary: regulator/academic paper text I read, or the project's own database.
- **[M]** my own calculation (Black-Scholes/Bachelier model on stated assumptions, or analysis of the project DB). Inference, not external evidence.
- **[V]** vendor / blog / marketing: flagged each time.
- **[S]** secondary summary only (could not open the primary text).

Headline caveat: there is no published, OPRA-based study of SPY/QQQ 0DTE quoted/effective spreads by moneyness and time of day. Almost all 0DTE academic work is SPX/SPXW. The best SPY/QQQ numbers I could get are from the project's own quote log (section 1.1), which has real limits (15-min delayed Yahoo quotes, 37 sessions, near-ATM strikes only). Everything on OTM strikes and the last 14 minutes is extrapolation.

---

## 1. Decision summary (what to plug into the simulator)

1. **Costs are small on SPY/QQQ ATM, and fees can matter more than spread.** SPY ATM is 1 cent wide ~95% of the time through 15:30; QQQ ATM ~77-88%. Round-trip spread cost for a crossing single-leg ATM option is ~0.9% of premium at 10:00, rising to ~2% by 15:00 as premium decays (same 1 cent, smaller premium). A $0.65/contract broker fee (IBKR Pro tiered) is 1.3 cents per share round trip per leg, bigger than the spread.
2. **Structure: default arm = single long call/put, delta ~0.45-0.55 (ATM or 1 strike ITM).** Narrow ($1-2) verticals cost 4-8x more per unit of directional exposure than a single; wide ($5) verticals cost about the same as a single per unit of delta but carry less gamma, and give up convexity. Credit vs debit at the same strikes is nearly a synthetic equivalent (put-call parity); the real choices are net delta, width and execution path, not "debit vs credit".
3. **Avoid cheap OTM.** Penny tick means a 1 cent spread is 10-13% of a $0.05-0.10 option and 3% of a $0.30 option. SPX evidence says OTM 0DTE calls have the most negative average returns.
4. **No structure rescues a directional signal with no edge.** Under a martingale all fairly-priced structures have expected P&L = minus costs. The structure choice only changes (a) cost per unit of delta, (b) leverage and stop/target geometry, (c) win-rate vs payoff shape.
5. **Time of day:** 10:00-14:00 is the cheapest window in relative terms (consistent with SPX academic work); after ~14:30 premium decay makes the fixed 1-cent spread a bigger fraction, and 0DTE ATM options lose value non-linearly (see 4.3).

---

## 2. Quoted and effective spreads

### 2.1 Project's own quote log (SPY/QQQ 0DTE, near-ATM) [P/M]
Source: `quote_snapshots` table in `C:\Users\slavk\0DTE-data\0dte.db` (read-only), 2026-07-31 to 2026-10-20, 37 sessions, ~10.9k rows each for SPY and QQQ; rows are calls/puts within about +/-$1.4 of spot (nearest 1-2 strikes either side of ATM, no true OTM strikes). Yahoo chain, consolidated bid/ask, **trails the share price by ~15 minutes** (see project memory "option-quotes-15min-delay"). I therefore date each quote at snapshot minute minus 15. Consequences: the final ~14 minutes of the session are NOT observed, and the first bucket can contain pre-open stale quotes (ignore it). Rows with spread >100 cents or <1 cent dropped (a handful of garbage rows, e.g. 252 cent spread). SPY spot averaged 768, QQQ 732 over the window.

Distribution of spread in cents (all times, ATM-ish):
| | 1 cent | 2 cents | 3-4 cents | >=5 cents |
|---|---|---|---|---|
| SPY | 95.4% | 4.3% | 0.25% | 0.12% |
| QQQ | 81.8% | 13.2% | 3.7% | 1.3% |

By quote time, ATM (|strike-spot| <= $0.5), medians unless noted. Mid = median option mid, cents spread = mean, "1c" = share of quotes exactly 1 cent, "3c+" = share >=3 cents:

| Clock (quote time) | SPY mid | SPY mean cents | SPY 1c | SPY spread % of mid (median / mean) | QQQ mid | QQQ mean cents | QQQ 1c | QQQ 3c+ | QQQ spread % (median / mean) |
|---|---|---|---|---|---|---|---|---|---|
| 10:00-10:30 | 1.23 | 1.08 | 93% | 0.85 / 0.99 | 1.65 | 1.35 | 77% | 6% | 0.75 / 0.86 |
| 10:30-11:30 | 1.10 | 1.05 | 95% | 0.94 / 1.03 | 1.51 | 1.33 | 78% | 6% | 0.77 / 0.88 |
| 11:30-12:30 | 0.96 | 1.04 | 97% | 1.07 / 1.36 | 1.25 | 1.16 | 88% | 2% | 0.89 / 1.10 |
| 12:30-13:30 | 0.90 | 1.03 | 97% | 1.14 / 1.32 | 1.07 | 1.30 | 83% | 4% | 1.06 / 1.19 |
| 13:30-14:30 | 0.70 | 1.11 | 95% | 1.50 / 1.80 | 0.87 | 1.34 | 87% | 3% | 1.27 / 1.51 |
| 14:30-15:00 | 0.57 | 1.05 | 96% | 1.77 / 2.20 | 0.75 | 1.22 | 85% | 4% | 1.60 / 1.95 |
| 15:00-15:30 | 0.49 | 1.04 | 96% | 2.11 / 2.57 | 0.58 | 1.19 | 88% | 3% | 1.94 / 2.47 |
| 15:30-15:46 | 0.48 | 1.05 | 95% | 2.11 / 3.32 | 0.56 | 1.53 | 77% | 9% | 2.25 / 3.45 |

Reading: in cents, SPY barely moves all day; QQQ is wider and noisier (about 1.2-1.5 cents average, 1-in-8 quotes 2 cents). In percent of premium the spread is lowest 10:00-12:00 (~0.75-0.95%) and roughly doubles by 15:00 because the premium halves. Up to 15:46 there is no sign of the "$0.15-0.40 in the last 15 minutes" widening claimed by one vendor (section 2.4); the very last 14 minutes remain unmeasured.

Spread % of mid by premium level, all ATM-ish rows (this is the useful proxy for OTM strikes, since a penny tick makes % spread = 1c / premium):
| Mid premium | SPY median / mean spread % | QQQ median / mean spread % |
|---|---|---|
| up to $0.10 | 13.3 / 14.1 | 11.2 / 15.2 |
| $0.10-0.20 | 6.1 / 6.5 | 6.3 / 6.6 |
| $0.20-0.40 | 3.2 / 3.4 | 3.3 / 3.4 |
| $0.40-0.80 | 1.65 / 1.75 | 1.65 / 1.78 |
| $0.80-1.50 | 0.96 / 0.99 | 0.96 / 1.07 |
| $1.50-3.00 | 0.56 / 0.59 | 0.59 / 0.73 |

Volatile days (days split into terciles by the day's spot range; wild = SPY range ~1.13%, QQQ ~1.7%; calm = SPY ~0.38%, QQQ ~0.56%; ATM, quote time after 10:00):
| | calm | middle | wild |
|---|---|---|---|
| SPY mean cents | 1.05 | 1.03 | 1.10 |
| SPY share >=3c | 0.2% | 0.1% | 0.4% |
| QQQ mean cents | 1.14 | 1.23 | 1.49 |
| QQQ share >=3c | 1.8% | 3.9% | 7.0% |
| QQQ share exactly 1c | 89% | 85% | 76% |
In percent terms wild days look cheaper (premiums are 30-40% higher). QQQ's wild-day widening is concentrated 13:30-15:00 (mean 1.66 cents). SPY is essentially insensitive in this sample. Only ~12 "wild" days per tercile, so treat as indicative; no FOMC/CPI-day split was run.

Limits: 37 sessions in a mostly rising/quiet Q3-Q4 2026 tape; polled snapshots, not time-weighted; Yahoo NBBO only; no strikes beyond ~$1.4 from spot; no exchange-level or size data; spread is a quoted spread (effective spread is smaller or equal, see section 3).

### 2.2 Structural facts
- SPY, QQQ and IWM options are quoted in **$0.01 increments at every premium level** (exception to the normal Penny Pilot $0.05-above-$3 rule). [Exchange rule filings, via search summary; 2014-2020 filings, not re-verified for 2026.] SPXW ticks are $0.05 (under $3) / $0.10 (over $3), so a one-tick SPXW market is 5-10 cents. This is the main reason SPY/QQQ quoted spreads are so narrow relative to SPX.
- With a 1-cent market there is no midpoint on the tick grid. "Limit at mid" is only possible when the market is >= 2 cents wide, or via a price-improvement auction. In my data that is 4% of the time for SPY, 18% for QQQ.
- Contract economics: 1 cent x 100 shares = $1 per contract per crossing. SPY/QQQ contract notional is ~$77k/$73k; an SPXW contract is ~$680k.

### 2.3 External numbers (mostly SPX, for contrast)
- **SEC Division of Trading and Markets, "Roundtable on Options Market Structure Supporting Data" (9 Apr 2026)** [P, OPRA/CAT through Dec 2025]: median effective spread for **ETP options** (all expiries, all ETPs) 1.4% at end-2025, down from 1.9% in 2012; trade-weighted quoted spread for the top-10 ETPs 1.7% in both 2012 and 2025. 0DTE is 28% of total options contract volume (19.6% at start of 2022); on expiry day individual customers are 73.5% of volume; SPX, SPY, QQQ top the 0DTE retail list by contracts and dollars. Consistent with my ~1% ATM figure at 10:00 for a tighter, 0DTE-heavy subset.
- **Almeida, Freire, Hizmeri, "0DTE Asset Pricing" (23 May 2025)** [P, SPX]: relative bid-ask spread is "relatively stable at its minimum between 10:00 and 14:00"; highest at the open and close (when volume is also highest). Same shape as table 2.1.
- **Doshi, Pederzoli, Sert, "Risky Intraday Order Flow and Option Liquidity" (May 2025 draft)** [P, SPX ATM, OPRA trades]: average effective spread ranges from a maximum of 9% for 0DTE to a minimum of 2% for 21-48 DTE; spreads follow the usual U-shape (wide at open and close); spreads widen on days when intraday order flow is volatile and the sensitivity is strongest for the shortest maturities. Sample period not checked.
- **Beckmeyer, Branger, Gayda (Dec 2023 version)** [P, SPX 0DTE, Feb 2021-Sep 2023, Table 1]: quoted / effective spread by time to expiry, retail (price-improved) vs non-retail:
  | Time to expiry | Retail QS / ES | Non-retail QS / ES |
  |---|---|---|
  | 3-24 h | 9.6% / 4.5% | 15.6% / 8.4% |
  | 1-3 h | 11.4% / 5.9% | 19.3% / 11.4% |
  | < 1 h | 19.0% / 10.5% | 31.0% / 21.1% |
  By moneyness (log K/S): ATM calls 6.2% / 3.0% (retail), calls 0.5-2% OTM 24.5% / 12.8%, calls >2% OTM 98% / 80%; ATM puts 5.7% / 2.7%, puts 0.5-2% OTM 14.5% / 7.0%. Hence cost % explodes with OTM distance and with proximity to expiry, but in SPX where the tick is 5-10 cents. Caveat: the SEC DERA paper (Fu, Li, Musto, Pearson 2025) argues OPRA trades are mis-sequenced against the following BBO in this kind of analysis, so effective-spread levels here may be mismeasured.
- **Vilkov, "0DTE Trading Rules" replication repo (corrected Aug 2026)** [P-code/KNOWN-ISSUES, SPXW 2016-Jan 2026]: after a unit error was fixed, mean bid-ask half-spread charged is ~2.2 bp of spot (~$1.33 mean dollar spread, averaged over strikes within +/-2% of ATM). SPY's 1 cent at 768 is ~0.13 bp full spread, so per unit of notional SPY/QQQ spreads are on the order of 10-15x tighter than SPXW (my arithmetic: SPXW ~$13 per SPY-equivalent notional vs SPY 1 cent). Not the same moneyness mix, so directional only.
- **Vendor/blog data points (all [V], none cite datasets):** FlashAlpha says SPY 0DTE ATM spreads are usually $0.02-0.05 and widen to $0.15-0.40 in the last 15 minutes; others say $0.01-0.03; Pineify-type sites say QQQ weeklies near the money are $0.05-0.08 wide. My data say SPY 1 cent (95%) and QQQ mostly 1 cent through 15:46, so the first and third are too wide for the measured window. Schaeffer's (analyst note, one day, end-of-day quotes 5 minutes before the close, Sep 2023): SPY 0DTE ~5.8% average spread vs ~1% for later expiries, and an ATM-ish example at $0.14/$0.15 (6.7%). That example is a near-close, ~$0.15 option, which matches my premium-bucket table (6% at $0.10-0.20), so it is a premium effect, not a SPY-specific widening.
- Volos Software (vendor, NDX index options): spreads highest near the close, driven by expiring ITM options, ~6 bp of spot; assumes 25% of the spread as slippage. Not ETF; ignore for sizing.

### 2.4 Time of day summary for the simulator
- Cents: SPY flat ~1.05; QQQ ~1.2-1.35 average with fat tail; first 15-30 min and the final minutes are wider (open: stale/auction effects; close: unmeasured here).
- Percent: scales as 1 cent / premium. Use the premium path in section 7.

---

## 3. Fill quality and price improvement for retail orders

Key point: **options have no Rule 605 execution-quality statistics**. Huang & Schwarz (Cornell/UC Irvine), SEC comment letter 16 Apr 2026 [P]: "options markets are currently operating in the pre-Rule 605 world", and their paper's price-improvement statistics "are the only publicly available data on brokers' options price execution"; no broker voluntarily publishes them. Rule 606 gives PFOF per contract, not fills.

What exists:
1. **SEC D&T staff, CAT data, individual-customer orders, Dec 2025** [P] (Tables 9.1-9.4). Marketable orders:
   | NBBO width at submission | Electronic book PI rate | Single-leg auction PI rate | Effective spread (electronic / auction) |
   |---|---|---|---|
   | tightest bin (nickel or less) | 0% | 22% | $0.01 / $0.02 |
   | dime | 0% | 53% | $0.05 / $0.05 |
   | quarter | 1% | 51% | $0.10 / $0.10 |
   Electronic executions were 39.7% of individual-customer dollar volume; single-leg auctions 20%; the rest complex/multi-leg etc. For non-marketable limit orders, price improvement occurs 80-99% (electronic) and 96-99% (auction) of the time and effective spreads are much lower. **Implication for SPY/QQQ (1-cent markets): a marketable single-leg order electronically pays the full half-spread (0.5c vs mid) with ~0% price improvement; auctions improve a minority of tight-market orders.** I could not isolate SPY/QQQ in this table.
2. **Huang, Jorion, Schwarz, "Some Anonymous Options Trades Are More Equal Than Others"** (SSRN 4951825; cited as under R&R at JF) [P via SEC letter, S for paper]: ~7,000 market and marketable-limit orders, six brokers, ~two dozen symbols, Mar-Jun 2024. Execution varied across brokers by "almost the entire quoted spread" (E/Q range 0.89). Price improvement as % of NBBO spread ranged from 7.2% to 51.5% across the six brokers (the letter's Table 9 came out garbled in PDF text extraction, so these ranges are my reconstruction; broker-to-number mapping not attempted); PFOF ranged 0 to 65.8 cents per contract; correlation of PI with PFOF -0.91; share of trades routed to auctions ranged 25% to 76%; PI in auctions 21-64%, in auto-execution 2.5-11.5%. Round-trip cost 0%-7% of premium across brokers (seminar abstract). Conversion: E/Q = 1 - 2 x PI, so 10% PI means you still pay 80% of the half-spread... as the authors put it, 10% PI on a five-cent spread ~ $2.00 per contract expected cost. A broker's own report (Robinhood) says 9.3% PI equal-weighted on the symbols traded, 12.5% for all flow. Critique: CCMR (Jun 2025 staff report, industry-aligned) says the sample was market orders only and excludes >90% of retail broker orders [S]; the authors reply that marketable orders are ~62% of executed retail volume.
3. **Bryzgalova, Pavlova, Sikorskaya, JF 2023** [P, single-name + ETF options, Nov 2019-Jun 2021]: retail (SLIM-flagged) trades, quoted 13.7% / effective 6.7% on average; weekly (<1 week) options quoted 12.6% / effective 6.6%; effective spread declines sharply with dollar size of trade. Not 0DTE or ETF specific. Retail price improvement via PIM auctions, ~50% of volume in weeklies.
4. **Beckmeyer et al.** [P, SPX]: retail effective spreads are ~half of non-retail (calls 6.0% vs 12.5%, puts 5.0% vs 9.6%); 17.8% of retail trades print exactly at the midpoint (the quote-rule "mid" bin); 74% of retail orders are 1 contract; buys pay 8.4% effective vs sells 5.2% (market makers favour customer sells).
5. **Fu, Li, Musto, Pearson, "Hope at a Reasonable Price: Customer Use of Limit Orders in the 0DTE Market" (SEC DERA, Mar 2025)** [P, SPXW limit order book, Jul 2020-Sep 2023]: the most direct evidence on what a patient limit order earns, but in SPXW: customers are at the BBO more than half the time; ATM markets were one tick wide just 0.5% of the time at 1 pm in July 2020 and 64.7% of the time in July 2023 (2 ticks 35.3%). For a call under $3 with a 2-tick ($0.10) market, a customer limit at the midpoint filled 50% (early) to 62% (late period) of the time within one second; if you replace the unfilled order with a marketable one after a second, all-in cost was $0.028 early / $0.021 late versus $0.05 for crossing immediately (so "mid with a one-second timeout" captured roughly 45-60% of the half-spread). Adverse selection on unfilled orders was only $0.006-0.007. This does not transfer 1:1: SPY/QQQ markets are 1 tick (1 cent) wide 95%/82% of the time so there is no mid to post at.
6. **Practitioner fill logs** [V]: thetadesk (Substack, anonymous, SPXW): "roughly half of real 0DTE prints happen at the mid" on 10-wide put credit spreads; marketable-fill cost 6-8% of the mid credit at every entry time 9:35-3:00 (modelled from four years of prints, labelled upper bound); mid-fill trades ~breakeven, marketable-fill about -$5 per trade. FlashAlpha (vendor, SPY 7-60 DTE put spreads, 7 years 1-min data): switching from mid fills to a limit at combo ask + $0.04 cut CAGR 30-60%, fills were 4-7 cents worse than mid, and only 17-25% of posted limits filled with ~12-13 min average wait. Not 0DTE, not intraday-directional; shows how sensitive mid-fill backtests are.
7. **Muravyev & Pearson, "Options Trading Costs Are Lower than You Think" (RFS 2020)** [S]: effective spreads for traders who time execution are <40% of conventional estimates; overall ~25% below. Relevant only to fast algos; a signal-driven bot is not "timing" in this sense.

Simulator fill model I would use (all [M] but anchored above):
- Marketable (cross) entry/exit: pay half the quoted spread per leg per side (SPY ~0.53c, QQQ ~0.65c average, 0.75c on wild QQQ days), no price improvement. Retail auction PI of 10-50% of the spread is broker-dependent; default 0% for conservatism, run 25% as a sensitivity.
- Limit at mid: cannot exist in 1c markets; for 2c markets fill probability ~50-60% inside a few seconds from the SPXW analogue, with the miss paid at the touch plus ~0.5-1 cent adverse selection per share missed. Do not credit mid fills for ETF 1c quotes.
- Fees: Alpaca commission $0, regulatory ~ORF $0.02685 + OCC $0.02 per contract (both sides), TAF $0.00279 on sells, plus CAT [Alpaca support/regulatory-fees pages, rates "subject to change", not date-verified] = roughly $0.05 per contract-side. IBKR Pro tiered: $0.65 per contract, $1.00 minimum per order, plus pass-throughs [IBKR commissions page, secondary summary]. Per share per leg-side: 0.05c (Alpaca) vs 0.65c (IBKR). Compare to the 0.5c half-spread.

---

## 4. Structure comparison for a same-day directional view

### 4.1 Theory in one paragraph
Under any fair-priced model with zero drift, expected P&L before costs is zero for every structure, so structure only reshapes the distribution. Same-day ATM gamma is huge and theta accelerates (value of an ATM option scales roughly with sqrt(time left), so it loses ~9% of its 10:00 value in the first hour but ~30-40% of its remaining value in the last hour). A long single is long gamma and long vega; a debit vertical is long a smaller amount of both (the short leg cancels part of the gamma/theta); a credit vertical with the short leg at/near the money is short gamma and positive theta, with win rate 55-67% but max loss 1.3-7x the credit. A bull call debit vertical and a bull put credit vertical at the same strikes are synthetically near-identical (put-call parity): same delta (~0.13 for a $1-wide ATM spread on SPY at 10:00), same max/min payoff structure up to the cash flow, differing only in costs, margin, early exercise/assignment and which leg's liquidity you hit. SPY/QQQ are American-style, physically settled: a short ITM leg can be exercised early (more so around dividends) and is assigned at expiry; SPXW is cash settled, so this risk has no SPX analogue.

### 4.2 Model numbers [M]
Model: Bachelier (normal) with r=0, flat variance per hour, no skew, spot 768 (SPY) or 717 (QQQ) at 10:00 with 6.0 hours left; sigma for the remaining time set so the ATM call is $1.20 (SPY, sigma_T = $3.00 = 0.39% of spot) or $1.50 (QQQ, sigma_T = $3.75 = 0.52%), which matches the log in 2.1. Costs: 0.5 cent per leg at entry and at exit (1 cent wide markets), no fees. Position held 1 hour, then exited.

SPY (S=768, K0=768):
| Structure | Premium (debit) or credit | Net delta | Max risk | Breakeven at expiry (vs spot) | P(profit at expiry, zero drift) | Round-trip spread cost in cents per 1.00 delta-share | Delta*S/max risk ("leverage") |
|---|---|---|---|---|---|---|---|
| long ATM call | 1.20 | 0.50 | 1.20 | +0.16% | 35% | 2.0 | 321x |
| long K0+1 call | 0.76 | 0.37 | 0.76 | +0.23% | 28% | 2.7 | 372x |
| long K0+2 call | 0.45 | 0.25 | 0.45 | +0.32% | 21% | 4.0 | 428x |
| debit call vertical K0/K0+1 | 0.43 | 0.13 | 0.43 | +0.06% | 44% | 15.3 | 231x |
| debit call vertical K0/K0+2 | 0.74 | 0.25 | 0.74 | +0.10% | 40% | 8.1 | 256x |
| debit call vertical K0/K0+5 | 1.14 | 0.45 | 1.14 | +0.15% | 35% | 4.4 | 305x |
| credit put vertical sell K0 / buy K0-1 | 0.43 credit | 0.13 | 0.57 | -0.06% | 56% | 15.3 | 177x |
| credit put vertical sell K0 / buy K0-2 | 0.74 credit | 0.25 | 1.26 | -0.10% | 60% | 8.1 | 151x |
| credit put vertical sell K0 / buy K0-5 | 1.14 credit | 0.45 | 3.86 | -0.15% | 65% | 4.4 | 90x |
| credit put vertical sell K0-1 / buy K0-2 (OTM) | 0.31 credit | 0.12 | 0.69 | -0.17% | 67% | 17.1 | 130x |

QQQ (S=717) is similar: costs per delta 2.0 (ATM), 2.5, 3.4, 19.0 ($1 vertical), 9.8 ($2), 4.9 ($5); breakevens +0.21% (ATM long), +0.06% ($1 debit vertical).

Interpretation [M]:
- The decisive metric is spread cost per unit of delta exposure. Narrow verticals net ~0.13 delta but pay four leg-crossings, so each unit of directional exposure costs 5-8x a single. With $5 width the penalty falls to ~2x. Cost in dollars is smaller for the narrow vertical in absolute terms, but you get proportionally much less exposure; if the signal edge scales with delta, that is the relevant comparison.
- This penalty may be overstated if verticals are sent as complex orders: Musto & Pearson (2023, cited in SEC DERA "Simple Roles for Complex Options") report buying verticals as complex orders rather than two single-leg trades can cut transaction costs by up to 83% [S, "up to"]; complex price-improvement auctions give the best price improvement relative to the net NBBO. Need own measurement (Alpaca multi-leg routing is unknown).
- Probability of profit is a trap: it rises from 35% (long ATM) to 65% (5-wide credit) while payoff skew flips; with zero drift expected P&L is identical before costs.
- Breakeven vs a typical one-hour move: 1-sigma move over 1 hour is ~0.16% of spot with 6h of flat vol; long ATM breakeven at expiry is +0.16%, i.e. you need a roughly one-hour 1-sigma move in your direction by the close just to break even if you hold to expiry (but intraday exits do not need to hold to expiry, see section 5).

### 4.3 Empirical evidence on structure (mostly SPX; ETF evidence is nil)
- **Beckmeyer et al.** [P, SPX, retail, Feb 2021-Sep 2023], Tables 3-5: single puts/calls average margin-adjusted returns -5.6% / -9.4%, medians about -0.7% / -0.8%, 25%+ expire worthless; put/call spreads (debit and credit pooled) mean ~0.1% / -0.2%, **median +3.0% / +3.3%**, 12.7%/11.6% of retail trades; iron condors median +5.5%. Regression of margin-adjusted return: debit dummy -0.29 pp, complex-order dummy +0.17 pp (+0.08 with day fixed effects), higher IV -> lower returns. Retail gross (mid) loss ~$30M vs net >$125M by Sep 2023, i.e. >$90M is spread. Caution: they cannot split debit from credit spreads in the headline stats; positive medians partly reflect the VRP earned by selling wings.
- **Vilkov, "0DTE Trading Rules"** [P-code/changelog; SPXW 10:00-16:00 holds, Sep 2016-Jan 2026]: after the Aug 2026 cost-unit correction, unconditional Sharpe: bear put spread +0.48 (mid) -> -0.73 net of spread+0.5bp; straddle/strangle -0.27 -> -0.97; iron butterfly/condor -0.56 -> -2.67; risk reversal +0.65 -> +0.10; put ratio spread +1.06 -> -0.61. "No structure retains a materially positive net Sharpe." Earlier published versions (positive net Sharpe 0.82-0.93 for conditional baskets) are superseded. Static, unconditional, SPX: tells us about cost drag, not ETF edge. Also: ATM-to-OTM calls and most puts lose on average; ATM long directional spreads have typically negative median P&L; selling slightly OTM calls/puts is profitable in up to ~75% of observations (median) while average returns of most strategies are not statistically significant (Hull Tactical summary, [S]).
- **Almeida, Freire, Hizmeri 2025** [P, SPX]: calls have low returns that decline with strike and become highly negative OTM; puts negative, most negative OTM; writing naked OTM calls/puts is on average profitable; ATM delta-hedged calls and straddles have negative average returns; the apparent "mispricing" was profitable before 2022 and dissipates once 0DTEs became daily.
- **OptionMetrics blog** [V]: same-day SPX covered-call sale returns rise monotonically with strike distance (ATM 6.8% to 1.0% OTM 14.2% annualised, 2018-present); says nothing on buying.
- **No head-to-head study of 0DTE long vs debit vs credit on SPY/QQQ with realistic fills exists** in what I could find. ORATS, Option Alpha and others sell intraday backtesters (SPY, QQQ supported on 1-minute data) but I found no published results; vendor snippets (apexvol: 24 SPY trades) are too small to use.

---

## 5. Exits and multi-leg interaction [M unless stated]

Theta path at unchanged spot (value as % of 10:00 value; hours left 6 / 5 / 4 / 3 / 2 / 1 / 0.5):
| Structure | 6h | 5h | 4h | 3h | 2h | 1h | 0.5h |
|---|---|---|---|---|---|---|---|
| long ATM call | 100% | 91% | 82% | 71% | 58% | 41% | 29% |
| long K0+2 call (OTM) | 100% | 82% | 63% | 43% | 24% | 6% | 1% |
| debit vertical $1 wide | 100% | 99% | 97% | 94% | 89% | 80% | 67% |
| debit vertical $5 wide | 100% | 93% | 84% | 74% | 61% | 43% | 30% |
(credit verticals mirror their debit twins: short-ATM $1 put spread behaves like the $1 debit call vertical in value terms.)
Check against data: the log's SPY ATM mid at 15:15 ($0.485) vs sqrt-time prediction ($0.44) and 12:00 ($0.96 vs 0.89) suggests the sqrt(time) rule is a good first-order decay model with a slightly slower-than-sqrt tail late in the day (IV tends to be bid into the close).

Underlying move needed to hit the project's current exit settings (+50% target, -35% stop on premium; for credit verticals the base is max risk) within 30 minutes of an entry at 10:00 (static mid values):
| Structure | +50% at | -35% at |
|---|---|---|
| long ATM call | +0.146% | -0.108% |
| long K0+2 OTM call | +0.118% | -0.072% |
| debit vertical $1 | +0.211% | -0.152% |
| debit vertical $5 | +0.153% | -0.114% |
| credit put vertical $1 (K0/K0-1) | +0.322% | -0.192% |
| credit put vertical $5 | unreachable | -0.323% |
1-sigma move in 30 minutes with flat vol is ~0.11% of spot; the 1-minute sigma is ~0.02%. Reading: a -35% stop on a single is 1 sigma away and OTM singles sit ~0.7 sigma away, so frequent stop-outs on noise; narrow verticals stop out less often per unit of premium but also reach targets less. The same percentage exit rule therefore does NOT mean the same underlying move; any exit-rule comparison across structures must be run in terms of underlying move/risk, not equal percentages. A +50% target on a credit spread (profit = 50% of max risk) is a very different event from +50% on a debit.

Exit costs: single-leg exit costs half the spread again (0.5c); a two-leg package pays ~1c unless executed as a complex order at or near the net mid. Expiry-day wrinkle: an ITM short leg near the close is exposed to exercise risk and wide late markets (SPX evidence: expiring ITM options carry the widest spreads, Volos [V]); closing credit spreads before ~15:30 avoids pinning/assignment but pays the larger late-day percentage spread. Legging out of a vertical (two separate orders) can cost materially more than the complex order (Musto & Pearson up to 83% saving, [S]).

---

## 6. Moneyness / delta choice for buyers

- **Cost side (strongest evidence):** penny tick => % spread = 1c/premium. SPY/QQQ premium buckets: up to $0.10: 11-14% spread; $0.10-0.20: ~6%; $0.20-0.40: ~3.3%; $0.40-0.80: ~1.65%; $0.80-1.50: ~1.0%; $1.50-3.00: ~0.6% (section 2.1, [M]). In SPX Beckmeyer Table 1 shows the same pattern at tick sizes 5x larger: OTM >2% calls 98% quoted spread. A rule of thumb: refuse entries below ~$0.30 mid (spread > 3%, ~3x the theta cliff exposure) unless the strategy is explicitly a lottery.
- **Return side (SPX only):** Almeida et al.: OTM 0DTE calls have the most negative average returns, declining in strike; ATM straddles and delta-hedged calls negative; Vilkov: ATM-to-OTM calls and most puts lose on average. Retail concentrates >90% of volume in ATM or slightly OTM (Beckmeyer), where leverage is 58-72x for weekly ATM options (BPS, 2023). I found no ETF-specific returns-by-moneyness study.
- **Greeks side [M]:** a 2-strike OTM call at 10:00 has delta 0.25, premium $0.45, leverage 428x, breakeven +0.32% (2 sigma-hours) and only 21% chance to be in the money at expiry; after 3 hours it has lost 57% of its value with spot unchanged vs 29% for ATM. It has the lowest cost in dollars and the highest cost per unit of delta (4c vs 2c for ATM).
- **ITM [M]:** a 1-strike ITM call (K0-1) has delta ~0.63, premium ~$1.76, cost 1.6c per delta (vs 2.0c ATM), keeps 82% of its value after 3 hours at unchanged spot (ATM 71%); a 2-strike ITM call has delta ~0.75, premium ~$2.45, 1.3c per delta, keeps 90%. Lower gamma, so it needs a bigger move for the same % gain. Not measured empirically (the quote log has no ITM beyond about $1). Reasonable default arm to test alongside ATM.

---

## 7. Simulator parameter block (copy/paste starting point)

All numbers are for SPY/QQQ 0DTE, Q3-Q4 2026, from the project DB unless noted. Cost is paid per leg, per side.

```
tick = 0.01 (all strikes, all premiums)
half_spread_cents (mean, crossing):
  SPY: 0.53 (calm/mid/wild: 0.52/0.52/0.55); 
  QQQ: 0.60-0.65 (calm 0.57, wild 0.75); first 30 min and wild-day afternoon: use 0.9-1.0
spread_distribution_cents:
  SPY: P(1)=0.95 P(2)=0.043 P(3-4)=0.0025 P(>=5)=0.001
  QQQ: P(1)=0.82 P(2)=0.13  P(3-4)=0.037  P(>=5)=0.013
spread_pct_of_mid_by_premium (median): 0.075->11-13%, 0.15->6%, 0.30->3.3%, 0.60->1.65%, 1.15->0.96%, 2.2->0.57%
ATM mid by clock time (SPY, $ ; QQQ $), quote-time buckets (10:00 = bucket start):
  10:00 1.23 / 1.65 | 10:30 1.10 / 1.51 | 11:30 0.96 / 1.25 | 12:30 0.90 / 1.07
  13:30 0.70 / 0.87 | 14:30 0.57 / 0.75 | 15:00 0.49 / 0.58 | 15:30 0.48 / 0.56
  (model: ATM mid ~ P_open * sqrt(minutes_left / 390); P_open ~1.31 SPY / 1.9 QQQ in this sample)
price_improvement_default = 0% of spread on marketable orders (sensitivity 25%, 50%)
midpoint_limit_fill_probability = n/a when spread == 1c; 50-60% when spread >= 2c (SPXW analogue)
commission_per_contract_side: Alpaca ~0.05 (regulatory pass-through), IBKR Pro ~0.65+; use the broker actually used
structure_cost_per_unit_delta (cents, round trip, SPY 10:00): single ATM 2.0 | +1 OTM 2.7 | +2 OTM 4.0 | $1 vertical 15.3 | $2 8.1 | $5 4.4
```
Time-of-day premium decay: use sqrt-time for ATM, an exact Bachelier/BS repricing for OTM and verticals (the project's `analytics/option_sim.py` already does Black-Scholes repricing with a configurable lag; the Yahoo 15-minute lag is a bigger error source than the spread for any exit rule that reacts inside 15 minutes).

---

## 8. Gaps and what would settle them

1. True OPRA NBBO for SPY/QQQ 0DTE by minute, strike (ATM, +1, +2 OTM) and time of day, including the last 15 minutes and event days (FOMC/CPI). The Yahoo log cannot supply this. Alpaca keys (pending per project notes) or a Databento/ThetaData/Polygon pull would; compute time-weighted quoted spread in cents and % of mid, plus trade-based effective spread vs NBBO mid.
2. Real fills: place 1-lot paper/live marketable and limit orders in SPY/QQQ at the broker you will use and log fill vs NBBO mid, as Huang/Schwarz did. Options PI varies by up to ~0.9 of the spread across brokers, so published averages are not a substitute.
3. Complex-order execution for verticals at your broker (does it route to the complex book or auction, or leg in?). This swings the narrow-vertical cost penalty by up to 5x.
4. Any ETF-based returns by moneyness/structure with realistic fills: not found. The only way to get it is the project's own paired-arm experiment; given the expected-zero-before-cost property, differences between arms will mostly be costs plus exit-geometry effects and need many sessions to separate from noise.
5. Late-day (last 14 minutes) behaviour and pin/assignment effects for SPY/QQQ short legs. Not measured.

---

## 9. Source list (accessed 2026-10-20)

Primary / academic / regulatory:
- SEC Division of Trading and Markets, Roundtable on Options Market Structure Supporting Data (9 Apr 2026): https://www.sec.gov/files/roundtable-options-market-structure.pdf
- Huang and Schwarz, SEC comment letter File 4-887 (16 Apr 2026): https://www.sec.gov/comments/4-887/4887-753447-2321514.pdf ; paper: SSRN 4951825 (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4951825)
- Fu, Li, Musto, Pearson, "Hope at a Reasonable Price: Customer Use of Limit Orders in the 0DTE Market", SEC DERA (Mar 2025): https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf
- Li, Musto, Pearson, "Simple Roles for Complex Options", SEC DERA (Mar 2024 draft): https://www.sec.gov/files/dera-simp-rule-complx-opt-2503.pdf
- Beckmeyer, Branger, Gayda, "Retail Traders Love 0DTE Options... But Should They?" (Dec 2023): https://wp.lancs.ac.uk/fofi2024/files/2024/04/FoFI-2024-146-Leander-Gayda.pdf
- Bryzgalova, Pavlova, Sikorskaya, JF 2023: https://doi.org/10.1111/jofi.13285
- Almeida, Freire, Hizmeri, "0DTE Asset Pricing" (May 2025): https://www.fma.org/assets/docs/Derivatives2025/Almeida.pdf
- Doshi, Pederzoli, Sert, "Risky Intraday Order Flow and Option Liquidity" (May 2025): https://www.bauer.uh.edu/hdoshi/docs/DPS_May_2025.pdf
- Vilkov, "0DTE Trading Rules" replication package and KNOWN-ISSUES (Aug 2026 correction): https://github.com/vilkovgr/0dte-strategies ; SSRN 4641356
- Cboe product update, SPY/QQQ Tue/Thu weeklies: https://cdn.cboe.com/resources/product_update/2022/Update-Cboe-Options-to-List-SPY-and-QQQ-Tuesday-and-Thursday-Expiring-Weekly-Options.pdf
- Cboe EDGX exhibit on P.M.-settled index options (SPXW vs SPY market quality; tables not extractable): https://www.sec.gov/files/rules/sro/cboeedgx/2024/34-99300-ex3.pdf
- Project data: `quote_snapshots` in `C:\Users\slavk\0DTE-data\0dte.db`, `analytics/option_sim.py`, memory note on Yahoo 15-minute lag.

Secondary / vendor / blog (flagged [V] or [S] above):
- Schaeffer's Investment Research, 0DTE spreads (Sep 2023): https://www.schaeffersresearch.com/content/bgs/2023/09/22/0dte-options-are-higher-spreads-imminent
- Volos Software (NDX): https://volossoftware.com/insights/improving-0dte-trading-returns
- thetadesk Substack (SPXW entry-time study, Jun 2022-Aug 2026): https://thetadesk.substack.com/p/0dte-study-what-time-of-day-should
- FlashAlpha (SPY 7-60 DTE put spread grid, fill model; vendor): https://flashalpha.com/articles/spy-put-credit-spread-active-backtest-mm-fills-vrp-signal-drawdown-breaker ; and flashalpha.com/articles/0dte-spy-complete-intraday-playbook-same-day-options (claim of $0.15-0.40 last-15-minute spreads)
- OptionMetrics blog on same-day SPX covered calls: https://optionmetrics.com/?p=3003
- Hull Tactical and CXO Advisory summaries of Vilkov: https://hulltactical.com/2026/07/08/0dte-options-tiny-edge-huge-distribution/ , https://cxoadvisory.com/equity-options/0dte-strategy-outcomes
- Alpaca fee pages: https://alpaca.markets/support/what-are-the-fees-associated-with-options-trading ; IBKR commissions: https://www.interactivebrokers.com/commissions
- Muravyev and Pearson, RFS 2020 (abstract-level only): https://ideas.repec.org/a/oup/rfinst/v33y2020i11p4973-5014..html
