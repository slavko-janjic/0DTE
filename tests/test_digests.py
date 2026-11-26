from datetime import date, datetime, timedelta, timezone

import pytest

from digests import build, inputs
from llm import client
from storage import db as storage
from storage import digests as dstore
from storage import feeds as fstore

CONFIG = {"tickers": ["QQQ"], "market_hours": {"timezone": "America/New_York"},
          "feeds": {"watchlist": ["QQQ", "TSLA"]},
          "market_catalysts": [{"date": "2026-12-09", "time": "14:00", "label": "FOMC", "impact": "high"},
                               {"date": "2027-01-27", "time": "14:00", "label": "FOMC", "impact": "high"}],
          "market_holidays": ["2026-12-25"], "market_half_days": ["2026-11-27"]}


def make_db(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    return path


def iso(hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class FakeComplete:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, prompt, *, system, model, purpose, db_path):
        self.calls.append({"prompt": prompt, "purpose": purpose, "model": model})
        if self.fail:
            raise client.LlmError("rate limited")
        return client.LlmResult(text="**Headline.**\n- point", model=model,
                                usage={"output_tokens": 42}, cost_usd=0.001, duration_ms=900)


# --- inputs -------------------------------------------------------------------

def test_crowd_input(tmp_path):
    path = make_db(tmp_path)
    assert inputs.crowd(path, CONFIG) == ""
    fstore.insert_reddit_mentions(path, [
        {"filter": "all-stocks", "ticker": "NVDA", "name": "NVIDIA", "rank": 1, "mentions": 300,
         "upvotes": 900, "rank_24h_ago": 1, "mentions_24h_ago": 600},
        {"filter": "all-stocks", "ticker": "MU", "name": "Micron", "rank": 2, "mentions": 90,
         "upvotes": 50, "rank_24h_ago": 9, "mentions_24h_ago": 30},
        {"filter": "wallstreetbets", "ticker": "NVDA", "name": "NVIDIA", "rank": 1, "mentions": 250,
         "upvotes": 800, "rank_24h_ago": 1, "mentions_24h_ago": 500}])
    text = inputs.crowd(path, CONFIG)
    assert "1. NVDA (NVIDIA): 300 vs 600 (-50%)" in text
    assert text.index("MU: 90 vs 30 (+200%)") < text.index("NVDA: 300 vs 600", text.index("RISERS"))
    assert "TOP OF r/wallstreetbets:\nNVDA 250" in text


def test_news_and_filings_inputs(tmp_path):
    path = make_db(tmp_path)
    assert inputs.news(path, CONFIG) == "" and inputs.filings(path, CONFIG) == ""
    fstore.insert_news_items(path, [{"id": "1", "source": "cnbc", "title": "Fed holds",
                                     "published_at": iso(1), "tickers": []}])
    fstore.insert_filings(path, [{"accession": "a", "ticker": "TSLA", "cik": 1, "form": "8-K",
                                  "filed_on": date.today().isoformat(), "description": "8-K: results",
                                  "link": "x"}])
    assert "| cnbc | - | Fed holds" in inputs.news(path, CONFIG)
    assert "| TSLA | 8-K | 8-K: results" in inputs.filings(path, CONFIG)


def test_market_input(tmp_path):
    path = make_db(tmp_path)
    assert inputs.market(path, CONFIG) == ""
    storage.insert_signal_snapshot(path, "QQQ", "bullish", 60.0, 0.6, "Buy calls",
                                   {"technicals": 0.6}, spot_price=450.25)
    text = inputs.market(path, CONFIG)
    assert "QQQ at" in text and "spot 450.25" in text and "technicals +0.60" in text
    assert "coin flip" in text


def test_calendar_input_window_and_earnings():
    text = inputs.calendar(None, CONFIG, earnings_lookup=lambda t: "2026-11-30" if t == "TSLA" else None,
                           today=date(2026, 11, 26))
    assert "2026-11-27 | half day" in text
    assert "2026-11-30 | earnings | TSLA" in text
    assert "FOMC" not in text            # Dec 9 is outside the 7-day window
    empty = inputs.calendar(None, {"feeds": {}}, earnings_lookup=lambda t: None, today=date(2026, 6, 1))
    assert empty.startswith("No scheduled catalysts")


# --- build --------------------------------------------------------------------

def test_build_card_calls_model_and_stores(tmp_path):
    path = make_db(tmp_path)
    fstore.insert_news_items(path, [{"id": "1", "source": "s", "title": "t", "published_at": iso(1),
                                     "tickers": []}])
    fake = FakeComplete()
    out = build.build_card("news", path, {"digests": {"model": "haiku"}, **CONFIG}, complete=fake)
    assert out["ok"] and fake.calls[0]["purpose"] == "digest:news"
    assert "DATA:" in fake.calls[0]["prompt"]
    assert dstore.latest_digests(path)["news"]["content"].startswith("**Headline.**")


def test_build_card_without_data_skips_model(tmp_path):
    path = make_db(tmp_path)
    fake = FakeComplete()
    out = build.build_card("filings", path, CONFIG, complete=fake)
    assert out["ok"] and fake.calls == []
    assert "No data yet" in dstore.latest_digests(path)["filings"]["content"]


def test_failed_build_keeps_last_good_card(tmp_path):
    path = make_db(tmp_path)
    fstore.insert_news_items(path, [{"id": "1", "source": "s", "title": "t", "published_at": iso(1),
                                     "tickers": []}])
    build.build_card("news", path, CONFIG, complete=FakeComplete())
    out = build.build_card("news", path, CONFIG, complete=FakeComplete(fail=True))
    assert not out["ok"]
    assert dstore.latest_digests(path)["news"]["ok"] == 1
    assert dstore.latest_digests(path, ok_only=False)["news"]["error"] == "rate limited"


def test_build_all_respects_card_list(tmp_path):
    path = make_db(tmp_path)
    out = build.build_all(path, {**CONFIG, "digests": {"cards": ["filings", "bogus"]}},
                          complete=FakeComplete())
    assert [o["kind"] for o in out] == ["filings"]


# --- llm client + usage log ---------------------------------------------------

def test_complete_logs_usage(tmp_path, monkeypatch):
    path = make_db(tmp_path)

    async def ok(prompt, system, model, timeout):
        return client.LlmResult("hi", model, {"input_tokens": 100, "output_tokens": 20,
                                              "cache_read_input_tokens": 5}, 0.002, 1200)

    monkeypatch.setattr(client, "_query", ok)
    assert client.complete("p", system="s", model="haiku", purpose="digest:news", db_path=path).text == "hi"

    async def bad(prompt, system, model, timeout):
        raise client.LlmError("boom")

    monkeypatch.setattr(client, "_query", bad)
    with pytest.raises(client.LlmError):
        client.complete("p", system="s", model="haiku", purpose="digest:news", db_path=path)
    s = dstore.usage_summary(path)["digest:news"]
    assert s["calls"] == 2 and s["tokens_in"] == 105 and s["tokens_out"] == 20 and s["failed"] == 1
