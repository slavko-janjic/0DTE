"""yfinance wrapper: intraday prices, option chains, IV, and derived greeks.

yfinance doesn't provide delta/gamma directly, so we derive them from each
contract's implied volatility via Black-Scholes. Every public function here
catches its own exceptions and returns None (or an empty structure) on
failure - the worker loop must be able to tolerate yfinance flaking without
crashing the whole poll cycle.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

_RISK_FREE_RATE = 0.02  # rough constant, fine for short-dated 0DTE greeks


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


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


def get_option_chain(ticker: str, expiration: str | None = None) -> OptionChainSnapshot | None:
    try:
        tk = yf.Ticker(ticker)
        exp = expiration or get_nearest_expiration(ticker)
        if exp is None:
            return None
        chain = tk.option_chain(exp)
        spot = get_current_price(ticker)
        if spot is None:
            return None
        return OptionChainSnapshot(ticker=ticker, expiration=exp, spot=spot,
                                    calls=chain.calls, puts=chain.puts)
    except Exception:
        return None


def _years_to_expiration(expiration: str) -> float:
    exp_date = datetime.strptime(expiration, "%Y-%m-%d").replace(
        hour=21, minute=0, tzinfo=timezone.utc  # approx 4pm ET market close
    )
    seconds = (exp_date - datetime.now(timezone.utc)).total_seconds()
    return max(seconds, 60.0) / (365.0 * 24 * 3600)


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


def contract_quote(contract) -> tuple[float | None, float | None, float | None, float | None]:
    """(bid, ask, mid, spread_pct) for a chain row - the raw cost picture, with
    each leg None when that quote is missing/zero. Used to log what transacting
    actually costs, so the intraday cost curve can be measured over time."""
    return (
        _quote(contract, "bid"),
        _quote(contract, "ask"),
        _mid(contract),
        contract_spread_pct(contract),
    )


def find_contract_row(chain: "OptionChainSnapshot | None", option_type: str, strike: float):
    """The held contract's chain row by exact strike match, or None."""
    if chain is None:
        return None
    df = chain.calls if option_type == "call" else chain.puts
    match = df[df["strike"] == strike]
    return None if match.empty else match.iloc[0]


def find_contract_price(chain: "OptionChainSnapshot | None", option_type: str, strike: float) -> float | None:
    """Prices a held contract for exit/valuation - bid-side (see honest fills
    above), as opposed to find_atm_contract which picks a new contract to buy."""
    row = find_contract_row(chain, option_type, strike)
    return contract_exit_price(row) if row is not None else None
