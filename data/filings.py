"""SEC EDGAR collector: recent filings for the watchlist, plus the ticker
universe (SEC's company list) that every feed uses to spot tickers in text.

SEC asks every client to identify itself with a User-Agent that includes a
contact email and to stay under 10 requests a second; set
`feeds.filings.user_agent` in settings.yaml (or SEC_USER_AGENT). Without it
the collector refuses to run rather than get the home IP blocked.

ETFs that don't file as companies (QQQ, IWM) have no CIK and are skipped.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
DEFAULT_FORMS = ["8-K", "10-Q", "10-K", "4", "S-1", "SC 13D", "SC 13G"]
REQUEST_GAP_SECONDS = 0.2     # 5 requests a second, half SEC's limit

# What the common 8-K item numbers mean, so a digest can say "results" or
# "CEO change" without opening the filing.
EIGHT_K_ITEMS = {
    "1.01": "material agreement", "1.03": "bankruptcy", "2.01": "acquisition/disposal",
    "2.02": "results of operations", "2.05": "restructuring costs", "2.06": "impairment",
    "3.01": "delisting notice", "5.02": "officer/director change", "5.07": "shareholder vote",
    "7.01": "Reg FD disclosure", "8.01": "other events",
}


class MissingUserAgent(RuntimeError):
    pass


def user_agent(config: dict) -> str:
    ua = config.get("feeds", {}).get("filings", {}).get("user_agent")
    if not ua or "CHANGE_ME" in ua:      # the settings.yaml placeholder
        ua = os.environ.get("SEC_USER_AGENT")
    if not ua or "@" not in ua:
        raise MissingUserAgent("no SEC contact: set SEC_USER_AGENT (with an email) in secrets.env beside the database")
    return ua


def parse_company_tickers(payload: dict) -> list[dict]:
    return [{"ticker": v["ticker"].upper(), "cik": int(v["cik_str"]), "name": v.get("title")}
            for v in payload.values()]


def describe(form: str, items: str | None, doc_description: str | None) -> str | None:
    if form == "8-K" and items:
        named = [EIGHT_K_ITEMS.get(i.strip(), f"item {i.strip()}") for i in items.split(",") if i.strip()]
        return "8-K: " + ", ".join(named)
    return doc_description or None


def parse_submissions(payload: dict, ticker: str, cik: int, forms: set[str],
                      since: date) -> list[dict]:
    recent = payload.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(recent.get("form", [])):
        filed = recent["filingDate"][i]
        if form not in forms or filed < since.isoformat():
            continue
        acc = recent["accessionNumber"][i]
        doc = recent["primaryDocument"][i]
        items = (recent.get("items") or [None] * (i + 1))[i]
        desc_list = recent.get("primaryDocDescription") or []
        out.append({
            "accession": acc, "ticker": ticker, "cik": cik, "form": form, "filed_on": filed,
            "description": describe(form, items, desc_list[i] if i < len(desc_list) else None),
            "link": ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
        })
    return out


def fetch_symbols(config: dict, timeout: float = 30) -> list[dict]:
    import requests
    resp = requests.get(TICKERS_URL, headers={"User-Agent": user_agent(config)}, timeout=timeout)
    resp.raise_for_status()
    return parse_company_tickers(resp.json())


def collect(config: dict, cik_lookup, timeout: float = 20) -> tuple[list[dict], list[str]]:
    """Filings for each watchlist ticker in the lookback window. `cik_lookup`
    maps ticker -> CIK or None (storage.feeds.cik_for, bound to the db)."""
    import requests
    ua = user_agent(config)
    cfg = config.get("feeds", {}).get("filings", {})
    forms = set(cfg.get("forms", DEFAULT_FORMS))
    since = date.today() - timedelta(days=cfg.get("lookback_days", 14))
    out, errors = [], []
    for ticker in config.get("feeds", {}).get("watchlist", []):
        cik = cik_lookup(ticker)
        if cik is None:
            continue
        try:
            resp = requests.get(SUBMISSIONS_URL.format(cik=cik),
                                headers={"User-Agent": ua}, timeout=timeout)
            resp.raise_for_status()
            out.extend(parse_submissions(resp.json(), ticker, cik, forms, since))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ticker}: {exc}")
        time.sleep(REQUEST_GAP_SECONDS)
    return out, errors
