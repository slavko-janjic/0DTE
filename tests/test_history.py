"""data.history: Alpaca bar download (pagination, regular hours, no partial
years), key loading, and the on-disk cache. No network: a fake http client."""
import json
from datetime import date, datetime, timezone

import pytest

from data import history


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(dict(params))
        return self.pages.pop(0)


def _bar(iso):
    return {"t": iso, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}


def test_fetch_follows_pages_and_keeps_regular_hours():
    http = FakeHttp([
        FakeResponse({"bars": [_bar("2024-03-11T13:29:00Z"),        # 09:29 EDT: pre-market
                               _bar("2024-03-11T13:30:00Z")],        # 09:30 EDT
                      "next_page_token": "abc"}),
        FakeResponse({"bars": [_bar("2024-03-11T19:59:00Z"),        # 15:59
                               _bar("2024-03-11T20:00:00Z")],        # 16:00: after the bell
                      "next_page_token": None}),
    ])
    rows = history.fetch_bars("SPY", datetime(2024, 1, 1, tzinfo=timezone.utc),
                              datetime(2025, 1, 1, tzinfo=timezone.utc), ("k", "s"), http=http)
    assert [r[0].hour for r in rows] == [13, 19]
    assert http.calls[1]["page_token"] == "abc"
    assert http.calls[0]["feed"] == "sip" and http.calls[0]["adjustment"] == "all"


def test_a_failed_page_leaves_no_partial_year(tmp_path):
    http = FakeHttp([FakeResponse({"bars": [_bar("2024-03-11T13:30:00Z")],
                                   "next_page_token": "x"}),
                     FakeResponse({}, status=429)])
    with pytest.raises(RuntimeError):
        history.update_cache(tmp_path, "SPY", ("k", "s"), date(2025, 6, 1),
                             first_year=2024, http=http, log=lambda *_: None)
    assert list(tmp_path.glob("*")) == []


def test_finished_years_are_fetched_once(tmp_path):
    page = FakeResponse({"bars": [_bar("2024-03-11T13:30:00Z")], "next_page_token": None})
    http = FakeHttp([page, page, page])
    history.update_cache(tmp_path, "SPY", ("k", "s"), date(2025, 6, 1), first_year=2024,
                         http=http, log=lambda *_: None)
    assert len(http.calls) == 2                                   # 2024 and 2025
    history.update_cache(tmp_path, "SPY", ("k", "s"), date(2025, 6, 1), first_year=2024,
                         http=http, log=lambda *_: None)
    assert len(http.calls) == 3                                   # only the current year again
    rows = history.load_bars(tmp_path, "SPY")
    assert rows[0][0] == datetime(2024, 3, 11, 13, 30, tzinfo=timezone.utc)
    assert rows[0][4] == 1.5


def test_keys_from_file_or_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(history.MissingKeys, match="alpaca_keys.json"):
        history.load_keys(tmp_path)
    (tmp_path / "alpaca_keys.json").write_text(json.dumps({"key_id": "a", "secret_key": "b"}))
    assert history.load_keys(tmp_path) == ("a", "b")
    monkeypatch.setenv("ALPACA_KEY_ID", "env-a")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "env-b")
    assert history.load_keys(tmp_path) == ("env-a", "env-b")
