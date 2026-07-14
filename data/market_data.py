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


def get_premarket_quote(ticker: str, proxy: str | None = None) -> float | None:
    """Overnight / pre-market last price. When a futures proxy is given (index
    ETFs -> NQ/ES/RTY, which trade overnight) use its live price; otherwise pull
    extended-hours intraday bars and take the latest close. None on failure."""
    if proxy:
        return get_current_price(proxy)
    try:
        df = yf.Ticker(ticker).history(period="2d", interval="5m", prepost=True)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def get_overnight_range(ticker: str, proxy: str | None = None) -> tuple[float, float] | None:
    """(overnight_high, overnight_low) from extended-hours bars - best-effort.
    Uses the proxy's continuous session when given, else the ticker's own
    pre/post bars. None when unavailable."""
    symbol = proxy or ticker
    try:
        df = yf.Ticker(symbol).history(period="1d", interval="5m", prepost=True)
        if df is None or df.empty:
            return None
        return float(df["High"].max()), float(df["Low"].min())
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


def find_contract_price(chain: "OptionChainSnapshot | None", option_type: str, strike: float) -> float | None:
    """Looks up a specific held contract's latest price by exact strike match -
    used to price open positions live, as opposed to find_atm_contract which is
    only for picking a new contract to buy."""
    if chain is None:
        return None
    df = chain.calls if option_type == "call" else chain.puts
    match = df[df["strike"] == strike]
    if match.empty:
        return None
    return float(match.iloc[0]["lastPrice"])
