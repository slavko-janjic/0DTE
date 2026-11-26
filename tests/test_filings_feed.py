from datetime import date

import pytest

from data import filings

SUBS = {"filings": {"recent": {
    "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
    "filingDate": ["2026-11-25", "2026-11-20", "2026-09-01"],
    "form": ["8-K", "DEFA14A", "10-Q"],
    "items": ["2.02,9.01", "", ""],
    "primaryDocument": ["a.htm", "b.htm", "c.htm"],
    "primaryDocDescription": ["8-K", "DEFA14A", "10-Q"],
}}}


def test_parse_submissions_filters_forms_and_dates():
    out = filings.parse_submissions(SUBS, "TSLA", 1318605, {"8-K", "10-Q"}, date(2026, 11, 1))
    assert len(out) == 1
    f = out[0]
    assert f["form"] == "8-K" and f["filed_on"] == "2026-11-25"
    assert f["description"] == "8-K: results of operations, item 9.01"
    assert f["link"] == "https://www.sec.gov/Archives/edgar/data/1318605/000126000001/a.htm"


def test_parse_company_tickers():
    payload = {"0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."}}
    assert filings.parse_company_tickers(payload) == [
        {"ticker": "AAPL", "cik": 320193, "name": "Apple Inc."}]


def test_user_agent_required(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(filings.MissingUserAgent):
        filings.user_agent({})
    with pytest.raises(filings.MissingUserAgent):
        filings.user_agent({"feeds": {"filings": {"user_agent": "no email"}}})
    assert filings.user_agent({"feeds": {"filings": {"user_agent": "me a@b.c"}}}) == "me a@b.c"
    with pytest.raises(filings.MissingUserAgent):
        filings.user_agent({"feeds": {"filings": {"user_agent": "x (contact: CHANGE_ME@example.com)"}}})
    monkeypatch.setenv("SEC_USER_AGENT", "env e@f.g")
    assert filings.user_agent({"feeds": {"filings": {"user_agent": "CHANGE_ME@x"}}}) == "env e@f.g"


def test_collect_skips_tickers_without_cik(monkeypatch):
    import requests
    calls = []

    class Resp:
        def raise_for_status(self):
            pass
        def json(self):
            return SUBS

    monkeypatch.setattr(requests, "get", lambda url, **kw: calls.append(url) or Resp())
    monkeypatch.setattr(filings, "REQUEST_GAP_SECONDS", 0)
    cfg = {"feeds": {"watchlist": ["QQQ", "TSLA"],
                     "filings": {"user_agent": "t a@b.c", "lookback_days": 3650}}}
    out, errors = filings.collect(cfg, lambda t: {"TSLA": 1318605}.get(t))
    assert len(calls) == 1 and "CIK0001318605" in calls[0]
    assert {f["form"] for f in out} == {"8-K", "10-Q"} and errors == []
