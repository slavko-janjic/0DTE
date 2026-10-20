"""Years of 1-minute share-price bars for the research lab, cached on disk.

Source: Alpaca's market-data API on its free plan - full-market (SIP) 1-minute
bars back to 2016, free except for the most recent 15 minutes, which research
never needs. Plain `requests`; keys come from <data dir>/alpaca_keys.json
({"key_id": ..., "secret_key": ...}) or the ALPACA_KEY_ID / ALPACA_SECRET_KEY
environment variables - never from the repo.

Cache: <data dir>/history/{TICKER}_{YEAR}.csv.gz, regular-hours bars only,
UTC ISO timestamps. A finished year is fetched once; the current year is
re-fetched whole on each update (a few requests). A year is written to a temp
file and renamed, so a failed fetch never leaves a partial year behind.

Daily VIX (open/close) comes from Yahoo, which has decades of it for free.
"""
import csv
import gzip
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
ET = ZoneInfo("America/New_York")
FIRST_YEAR = 2016


class MissingKeys(RuntimeError):
    """No Alpaca keys in the key file or the environment."""


def history_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "history"


def load_keys(data_dir: Path) -> tuple[str, str]:
    key_id, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if key_id and secret:
        return key_id, secret
    key_file = Path(data_dir) / "alpaca_keys.json"
    if key_file.exists():
        keys = json.loads(key_file.read_text(encoding="utf-8"))
        if keys.get("key_id") and keys.get("secret_key"):
            return keys["key_id"], keys["secret_key"]
    raise MissingKeys(
        f"No Alpaca keys. Put {{\"key_id\": ..., \"secret_key\": ...}} in {key_file} "
        f"(free account at alpaca.markets -> API keys), or set ALPACA_KEY_ID and "
        f"ALPACA_SECRET_KEY.")


def _regular_hours(ts: datetime) -> bool:
    local = ts.astimezone(ET)
    minutes = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def fetch_bars(symbol: str, start: datetime, end: datetime, keys: tuple[str, str],
               http=requests, timeout: float = 60.0) -> list[tuple]:
    """All 1-minute bars in [start, end), following next_page_token, kept to
    regular hours. Rows: (utc datetime, open, high, low, close, volume).
    Raises on any HTTP error - a partial year must not be cached."""
    headers = {"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]}
    params = {"timeframe": "1Min", "start": start.isoformat(), "end": end.isoformat(),
              "limit": 10000, "adjustment": "all", "feed": "sip"}
    rows = []
    while True:
        response = http.get(ALPACA_BARS_URL.format(symbol=symbol), headers=headers,
                            params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        for bar in payload.get("bars") or []:
            ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
            if _regular_hours(ts):
                rows.append((ts, float(bar["o"]), float(bar["h"]), float(bar["l"]),
                             float(bar["c"]), float(bar["v"])))
        token = payload.get("next_page_token")
        if not token:
            return rows
        params["page_token"] = token


def year_file(cache: Path, symbol: str, year: int) -> Path:
    return Path(cache) / f"{symbol}_{year}.csv.gz"


def write_year(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with gzip.open(temp, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["t", "o", "h", "l", "c", "v"])
        for ts, o, h, l, c, v in rows:
            writer.writerow([ts.isoformat(), o, h, l, c, v])
    os.replace(temp, path)


def read_year(path: Path) -> list[tuple]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        return [(datetime.fromisoformat(t), float(o), float(h), float(l), float(c), float(v))
                for t, o, h, l, c, v in reader]


def update_cache(cache: Path, symbol: str, keys: tuple[str, str], today: date,
                 first_year: int = FIRST_YEAR, http=requests, log=print) -> list[int]:
    """Fetches every missing year, and the current year to date. Returns the
    years written."""
    written = []
    for year in range(first_year, today.year + 1):
        path = year_file(cache, symbol, year)
        if path.exists() and year < today.year:
            continue
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if year < today.year
               else datetime(today.year, today.month, today.day, tzinfo=timezone.utc))
        rows = fetch_bars(symbol, start, end, keys, http=http)
        write_year(path, rows)
        written.append(year)
        log(f"  {symbol} {year}: {len(rows):,} bars")
    return written


def load_bars(cache: Path, symbol: str) -> list[tuple]:
    rows = []
    for path in sorted(Path(cache).glob(f"{symbol}_*.csv.gz")):
        rows.extend(read_year(path))
    return rows


# --- daily VIX -------------------------------------------------------------------

def vix_file(cache: Path) -> Path:
    return Path(cache) / "vix_daily.csv"


def update_vix(cache: Path, log=print) -> int:
    """Daily ^VIX open/close since 2015 from Yahoo, rewritten whole (one call)."""
    import yfinance as yf
    df = yf.Ticker("^VIX").history(start="2015-06-01", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError("Yahoo returned no VIX history")
    path = vix_file(cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    with open(temp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "open", "close"])
        for ts, row in df.iterrows():
            writer.writerow([ts.date().isoformat(), float(row["Open"]), float(row["Close"])])
    os.replace(temp, path)
    log(f"  VIX daily: {len(df):,} days")
    return len(df)


def load_vix(cache: Path) -> dict:
    """{date: (open, close)}; empty when not fetched yet."""
    path = vix_file(cache)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {date.fromisoformat(r["date"]): (float(r["open"]), float(r["close"]))
                for r in reader}
