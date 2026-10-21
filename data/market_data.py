"""yfinance wrapper: intraday prices, option chains, IV, and derived greeks.

yfinance doesn't provide delta/gamma directly, so we derive them from each
contract's implied volatility via Black-Scholes. Every public function here
catches its own exceptions and returns None (or an empty structure) on
failure - the worker loop must be able to tolerate yfinance flaking without
crashing the whole poll cycle.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

_RISK_FREE_RATE = 0.02  # rough constant, fine for short-dated 0DTE greeks


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(spot: float, strike: float, sigma: float, years: float, option_type: str,
             rate: float = _RISK_FREE_RATE) -> float:
    """European Black-Scholes price. Intrinsic value when there is no time or
    no volatility left."""
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if spot <= 0 or strike <= 0 or sigma <= 0 or years <= 0:
        return intrinsic
    root = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / root
    d2 = d1 - root
    discount = strike * math.exp(-rate * years)
    if option_type == "call":
        return spot * _norm_cdf(d1) - discount * _norm_cdf(d2)
    return discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price: float, spot: float, strike: float, years: float, option_type: str,
                rate: float = _RISK_FREE_RATE, low: float = 0.01, high: float = 5.0) -> float | None:
    """The volatility that reproduces `price` (bisection). None when the price
    is at or below intrinsic, or beyond what `high` can explain - a quote that
    can't be inverted says nothing about volatility."""
    if price is None or price <= 0 or years <= 0:
        return None
    if price <= bs_price(spot, strike, low, years, option_type, rate):
        return None
    if price >= bs_price(spot, strike, high, years, option_type, rate):
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        if bs_price(spot, strike, mid, years, option_type, rate) < price:
            low = mid
        else:
            high = mid
        if high - low < 1e-7:
            break
    return (low + high) / 2.0


def black_scholes_greeks(
    spot: float, strike: float, iv: float, time_to_exp_years: float, option_type: str
) -> tuple[float, float] | None:
    """Returns (delta, gamma) for a European option. None on invalid inputs."""
    if spot <= 0 or strike <= 0 or iv <= 0 or time_to_exp_years <= 0:
        return None
    try:
        d1 = (
            math.log(spot / strike) + (_RISK_FREE_RATE + 0.5 * iv**2) * time_to_exp_years
        ) / (iv * math.sqrt(time_to_exp_years))
        gamma = _norm_pdf(d1) / (spot * iv * math.sqrt(time_to_exp_years))
        if option_type == "call":
            delta = _norm_cdf(d1)
        else:
            delta = _norm_cdf(d1) - 1.0
        return delta, gamma
    except (ValueError, ZeroDivisionError):
        return None


def get_current_price(ticker: str) -> float | None:
    try:
        fast_info = yf.Ticker(ticker).fast_info
        price = fast_info.get("lastPrice") or fast_info.get("last_price")
        return float(price) if price else None
    except Exception:
        return None


def get_intraday_bars(ticker: str, interval: str = "5m", period: str = "1d") -> pd.DataFrame | None:
    try:
        df = yf.Ticker(ticker).history(interval=interval, period=period)
        return df if not df.empty else None
    except Exception:
        return None


def get_daily_bars(ticker: str, period: str = "5d") -> pd.DataFrame | None:
    """Daily OHLC bars - used pre-market for prior-session High/Low/Close.
    Mirrors get_intraday_bars: None on failure or empty."""
    try:
        df = yf.Ticker(ticker).history(interval="1d", period=period)
        return df if not df.empty else None
    except Exception:
        return None


def get_premarket_quote(ticker: str) -> float | None:
    """Overnight / pre-market last price from the ticker's OWN extended-hours
    bars (latest close). None on failure.

    Previously accepted an index-futures proxy for the ETFs, but that returned
    an index-scale price (NQ=F ~28,727) which the caller then differenced
    against the ETF's own close (~696.7) - a units mismatch that produced a
    +4023% gap. The ETF's own pre/post closes are real and in the right units.
    """
    try:
        df = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=True)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def get_overnight_range(
    ticker: str, tz_name: str = "America/New_York",
) -> tuple[float, float] | None:
    """(high, low) of the OVERNIGHT session only: everything traded after the
    last regular-session bar, i.e. the prior close through the coming open.
    From the ticker's OWN extended-hours bars (a futures-proxy variant used to
    store index-scale levels on the ETF chart - see get_premarket_quote).

    Was previously max/min over a full `period="1d", prepost=True` window -
    which includes the regular session - so it reported the whole day's range
    mislabeled as "overnight" (QQQ: 719.83/681.27, a 5.4% span, against a ~706
    spot), and that wrong level was shown on the dashboard AND drawn on the
    price chart as a key level.

    Built from CLOSES, not High/Low. Extended-hours bars carry no volume and
    their High/Low fields are contaminated with bad ticks: a real SPY bar showed
    close=749.86, low=749.82 and high=754.68 on zero volume, and another printed
    a low of 701.68 against a ~750 median. Taking max(High)/min(Low) hoovered up
    exactly that garbage and reported a 7.5% overnight range for SPY. The closes
    are clean, and with almost no trading overnight they approximate the range
    honestly.

    Returns None during the regular session, when no overnight session is in
    progress - which is honest, rather than quietly handing back today's range.
    """
    try:
        df = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=True)
        if df is None or df.empty:
            return None
        local = df.tz_convert(tz_name) if df.index.tz is not None else df
        minutes = local.index.hour * 60 + local.index.minute
        is_regular = (minutes >= 570) & (minutes < 960)   # 09:30-16:00 ET
        if not is_regular.any():
            return None
        # everything after the last regular-session bar IS the overnight session
        last_regular = local.index[is_regular][-1]
        overnight = local[local.index > last_regular]
        closes = overnight["Close"].dropna()
        closes = closes[closes > 0]
        if closes.empty:
            return None
        return float(closes.max()), float(closes.min())
    except Exception:
        return None


def get_next_earnings_date(ticker: str) -> str | None:
    """Best-effort next earnings date (ISO) via yfinance calendar - used only to
    flag a single-name earnings catalyst, never depended on. None on any issue."""
    try:
        cal = yf.Ticker(ticker).calendar
        value = None
        if isinstance(cal, dict):
            value = cal.get("Earnings Date")
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
        elif cal is not None and hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
            value = cal.loc["Earnings Date"][0]
        if value is None:
            return None
        if hasattr(value, "date"):
            value = value.date()
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return None


def get_nearest_expiration(ticker: str) -> str | None:
    """Returns the nearest available expiration date string (0DTE if today's listed)."""
    try:
        expirations = yf.Ticker(ticker).options
        return expirations[0] if expirations else None
    except Exception:
        return None


@dataclass
class OptionChainSnapshot:
    ticker: str
    expiration: str
    spot: float
    calls: pd.DataFrame
    puts: pd.DataFrame
    # the share price the quotes actually reflect, and whether bid/ask/lastPrice
    # were repriced to `spot` (see correct_quote_delay); raw quotes stay in raw_*
    spot_at_quote: float | None = None
    delay_corrected: bool = False


# --- the 15-minute quote delay ------------------------------------------------
# Yahoo's option quotes trail the share price by 15 minutes. Measured 2026-10-19
# over ~9,000 one-minute quote pairs per ticker: changes in the ATM option mid
# correlate +0.69 with the share-price change 15 minutes EARLIER and ~0 with the
# same minute (SIGNAL_RESEARCH.md). The share price the signals read is not
# delayed, so every fill used to be priced off where the share stood 15 minutes
# before: a trade that followed the last 15 minutes of movement bought cheap and
# watched the stale quote "catch up" - a gain no real trade gets (it explained
# the paper edge of every follow-the-signal strategy).
QUOTE_DELAY_MINUTES = 15
_QUOTE_FIELDS = ("bid", "ask", "lastPrice")


_SPOT_BAR_MAX_GAP_MINUTES = 5


def get_spot_minutes_ago(ticker: str, minutes: int) -> float | None:
    """The share price `minutes` ago: the close of the last 1-minute bar that
    had finished by then. Pre/post-market bars included, so the open's first
    quarter-hour (whose quotes still reflect the pre-market) is covered too.
    None when Yahoo has no bar that old today, or when the latest one is more
    than a few minutes older than that - overnight the last bar is hours old,
    and "correcting" a quote by a move that happened hours ago is worse than
    leaving it alone."""
    try:
        bars = yf.Ticker(ticker).history(interval="1m", period="1d", prepost=True)
    except Exception:
        return None
    if bars is None or bars.empty:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes + 1)
    index = bars.index.tz_convert("UTC") if bars.index.tz is not None else bars.index
    mask = index <= cutoff
    older = bars[mask]
    if older.empty or index[mask][-1] < cutoff - timedelta(minutes=_SPOT_BAR_MAX_GAP_MINUTES):
        return None
    close = float(older["Close"].iloc[-1])
    return close if close > 0 else None


def correct_quote_delay(snapshot: OptionChainSnapshot, spot_at_quote: float | None,
                        delay_minutes: int = QUOTE_DELAY_MINUTES) -> OptionChainSnapshot:
    """Reprices a delayed chain to the current share price.

    For each contract: back out the volatility its mid implies at the share
    price (and time to expiry) the quote reflects, reprice at the current share
    price and time, and shift bid, ask and lastPrice by the difference - the
    quoted spread is kept, since the delay moves the price, not the cost of
    crossing it. Where the mid can't be inverted (deep in or out of the money)
    the shift is the change in intrinsic value. Prices never go below zero, and
    a bid that would is dropped to zero.

    The raw quotes are kept as raw_bid / raw_ask / raw_lastPrice so the delay
    itself stays measurable. Unchanged (delay_corrected False) when the old
    share price is unknown or the delay is 0."""
    if spot_at_quote is None or spot_at_quote <= 0 or delay_minutes <= 0:
        return snapshot
    years_now = _years_to_expiration(snapshot.expiration)
    years_then = years_now + delay_minutes / (365.0 * 24 * 60)
    spot_now = snapshot.spot

    def reprice(df: pd.DataFrame, option_type: str) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        df = df.copy()
        for field in _QUOTE_FIELDS:
            if field in df.columns:
                df[f"raw_{field}"] = df[field]
        for idx, row in df.iterrows():
            strike = _num_or_none(row.get("strike"))
            if strike is None:
                continue
            mid = _mid(row) or _quote(row, "lastPrice")
            if mid is None:
                continue
            sigma = implied_vol(mid, spot_at_quote, strike, years_then, option_type)
            if sigma is not None:
                shift = bs_price(spot_now, strike, sigma, years_now, option_type) - mid
            else:
                shift = (bs_price(spot_now, strike, 0.0, 0.0, option_type)
                         - bs_price(spot_at_quote, strike, 0.0, 0.0, option_type))
            for field in _QUOTE_FIELDS:
                value = _quote(row, field)
                if value is not None:
                    df.at[idx, field] = max(value + shift, 0.0)
        return df

    snapshot.calls = reprice(snapshot.calls, "call")
    snapshot.puts = reprice(snapshot.puts, "put")
    snapshot.spot_at_quote = spot_at_quote
    snapshot.delay_corrected = True
    return snapshot


def _num_or_none(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def get_option_chain(ticker: str, expiration: str | None = None,
                     quote_delay_minutes: int = QUOTE_DELAY_MINUTES) -> OptionChainSnapshot | None:
    """The nearest-expiry chain with its quotes repriced to the current share
    price (correct_quote_delay). Pass quote_delay_minutes=0 for the raw quotes
    - e.g. from a real-time source."""
    try:
        tk = yf.Ticker(ticker)
        exp = expiration or get_nearest_expiration(ticker)
        if exp is None:
            return None
        chain = tk.option_chain(exp)
        spot = get_current_price(ticker)
        if spot is None:
            return None
        # Yahoo sometimes answers with one side missing. That's a failed fetch,
        # not "no call volume" - callers read both frames unconditionally.
        if chain.calls is None or chain.puts is None:
            return None
        snapshot = OptionChainSnapshot(ticker=ticker, expiration=exp, spot=spot,
                                       calls=chain.calls, puts=chain.puts)
    except Exception:
        return None
    if quote_delay_minutes > 0:
        snapshot = correct_quote_delay(
            snapshot, get_spot_minutes_ago(ticker, quote_delay_minutes), quote_delay_minutes)
    return snapshot


def _years_to_expiration(expiration: str) -> float:
    """Time to the 16:00 ET close on the expiration date, in years (floored at a
    minute). Was a fixed 21:00 UTC, an hour late in daylight-saving time - which
    understated a 0DTE option's implied vol by ~7% at 10:00."""
    from zoneinfo import ZoneInfo
    exp_date = datetime.strptime(expiration, "%Y-%m-%d").replace(
        hour=16, minute=0, tzinfo=ZoneInfo("America/New_York"))
    seconds = (exp_date - datetime.now(timezone.utc)).total_seconds()
    return max(seconds, 60.0) / (365.0 * 24 * 3600)


def contract_iv(chain: "OptionChainSnapshot | None", row, option_type: str) -> float | None:
    """Implied volatility of a chain row's mid (lastPrice when there's no
    two-sided quote) at the chain's share price - the number that says how much
    movement the option was pricing when it was traded."""
    if chain is None or row is None:
        return None
    price = _mid(row) or _quote(row, "lastPrice")
    strike = _num_or_none(row.get("strike")) if hasattr(row, "get") else None
    if price is None or strike is None:
        return None
    return implied_vol(price, chain.spot, strike, _years_to_expiration(chain.expiration),
                       option_type)


def implied_move_pct(chain: "OptionChainSnapshot | None", strike: float) -> float | None:
    """The straddle at `strike` (call mid + put mid) as % of the share price -
    the market's expected move for the rest of the day."""
    if chain is None:
        return None
    call_row = find_contract_row(chain, "call", strike)
    put_row = find_contract_row(chain, "put", strike)
    if call_row is None or put_row is None:
        return None
    call_mid, put_mid = _mid(call_row), _mid(put_row)
    if call_mid is None or put_mid is None or not chain.spot:
        return None
    return (call_mid + put_mid) / chain.spot * 100.0


def enrich_with_greeks(snapshot: OptionChainSnapshot) -> OptionChainSnapshot:
    """Adds 'delta' and 'gamma' columns to calls/puts, derived from impliedVolatility."""
    tte = _years_to_expiration(snapshot.expiration)

    def add_greeks(df: pd.DataFrame, option_type: str) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        df = df.copy()
        deltas, gammas = [], []
        for _, row in df.iterrows():
            greeks = black_scholes_greeks(
                snapshot.spot, row["strike"], row.get("impliedVolatility", 0.0), tte, option_type
            )
            deltas.append(greeks[0] if greeks else None)
            gammas.append(greeks[1] if greeks else None)
        df["delta"] = deltas
        df["gamma"] = gammas
        return df

    snapshot.calls = add_greeks(snapshot.calls, "call")
    snapshot.puts = add_greeks(snapshot.puts, "put")
    return snapshot


_VOLATILITY_TICKERS = {"vix": "^VIX", "vix9d": "^VIX9D", "vvix": "^VVIX"}


def get_vix_term_structure() -> dict[str, float] | None:
    """Latest close for VIX, VIX9D, and VVIX. None if any leg is unavailable -
    the volatility_regime signal needs all three to be meaningful."""
    values = {name: get_current_price(ticker) for name, ticker in _VOLATILITY_TICKERS.items()}
    return values if all(v is not None for v in values.values()) else None


def find_atm_contract(df: pd.DataFrame, spot: float) -> pd.Series | None:
    """Returns the row whose strike is closest to spot (ATM), or None if df is empty."""
    if df is None or df.empty:
        return None
    idx = (df["strike"] - spot).abs().idxmin()
    return df.loc[idx]


def find_strike_contract(df: pd.DataFrame, spot: float, option_type: str,
                         itm_steps: int = 0) -> pd.Series | None:
    """The ATM row (nearest strike to spot), or the row `itm_steps` listed
    strikes deeper in the money from it: lower strikes for a call, higher for a
    put. None when the chain doesn't list that strike."""
    atm = find_atm_contract(df, spot)
    if atm is None or itm_steps == 0:
        return atm
    strikes = sorted(set(df["strike"].tolist()))
    i = strikes.index(atm["strike"])
    j = i - itm_steps if option_type == "call" else i + itm_steps
    if not 0 <= j < len(strikes):
        return None
    return df[df["strike"] == strikes[j]].iloc[0]


# --- honest fills ------------------------------------------------------------
# Paper fills used to use lastPrice - the most optimistic assumption possible.
# 0DTE spreads are wide and last-trade can be minutes stale, so buys fill at the
# ask and sells at the bid (crossing the spread), falling back to the mid and
# only then to lastPrice when quotes are missing/zero (common after hours).

def _quote(contract, field: str) -> float | None:
    try:
        value = contract.get(field)
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if value is None or math.isnan(value) or value <= 0:
        return None
    return value


def _mid(contract) -> float | None:
    bid, ask = _quote(contract, "bid"), _quote(contract, "ask")
    if bid is not None and ask is not None and ask >= bid:
        return (bid + ask) / 2.0
    return None


def contract_entry_price(contract) -> float | None:
    """Buy fill: ask -> mid -> lastPrice."""
    return _quote(contract, "ask") or _mid(contract) or _quote(contract, "lastPrice")


def contract_exit_price(contract) -> float | None:
    """Sell fill / open-position valuation: bid -> mid -> lastPrice."""
    return _quote(contract, "bid") or _mid(contract) or _quote(contract, "lastPrice")


def contract_spread_pct(contract) -> float | None:
    """Bid/ask spread as a percent of the mid - the round-trip cost the honest
    fills bake in. None when either quote is missing."""
    bid, ask, mid = _quote(contract, "bid"), _quote(contract, "ask"), _mid(contract)
    if bid is None or ask is None or not mid:
        return None
    return (ask - bid) / mid * 100.0


def find_delta_contract(df: pd.DataFrame, target_delta: float) -> pd.Series | None:
    """The contract whose delta is closest to target_delta - e.g. +0.25 for an
    OTM call, -0.25 for an OTM put. Requires enrich_with_greeks() to have run.

    Real IV skew is measured ACROSS strikes (are traders paying up for crash
    protection or for upside?). Comparing a call and put at the SAME strike is
    meaningless: put-call parity pins them to the same IV, so the difference is
    pure quote noise. This is how you pick the two sides honestly.
    """
    if df is None or df.empty or "delta" not in df:
        return None
    valid = df[df["delta"].notna()]
    if valid.empty:
        return None
    idx = (valid["delta"] - target_delta).abs().idxmin()
    return valid.loc[idx]


def contract_quote(contract, raw: bool = False) -> tuple[float | None, float | None, float | None, float | None]:
    """(bid, ask, mid, spread_pct) for a chain row, each leg None when that
    quote is missing/zero. Used to log what transacting actually costs, so the
    intraday cost curve can be measured over time. raw=True reads the quotes
    as Yahoo sent them (before correct_quote_delay), when the row has them."""
    if raw and "raw_bid" in getattr(contract, "index", ()):
        contract = {"bid": contract.get("raw_bid"), "ask": contract.get("raw_ask")}
    return (
        _quote(contract, "bid"),
        _quote(contract, "ask"),
        _mid(contract),
        contract_spread_pct(contract),
    )


def find_contract_row(chain: "OptionChainSnapshot | None", option_type: str, strike: float,
                      expiration: str | None = None):
    """The held contract's chain row by exact strike match, or None.

    Pass the held contract's expiration: the chain is always the NEAREST
    expiry, so once a position's own date has passed the same strike belongs
    to a different contract - pricing (and closing) against it is wrong."""
    if chain is None:
        return None
    if expiration is not None and chain.expiration != expiration:
        return None
    df = chain.calls if option_type == "call" else chain.puts
    match = df[df["strike"] == strike]
    return None if match.empty else match.iloc[0]


def find_contract_price(chain: "OptionChainSnapshot | None", option_type: str, strike: float,
                        expiration: str | None = None) -> float | None:
    """Prices a held contract for exit/valuation - bid-side (see honest fills
    above), as opposed to find_atm_contract which picks a new contract to buy."""
    row = find_contract_row(chain, option_type, strike, expiration)
    return contract_exit_price(row) if row is not None else None
