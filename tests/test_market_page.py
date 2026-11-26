from datetime import datetime, timedelta, timezone

from storage import db as storage
from storage import digests as dstore
from storage import feeds as fstore
from webapi import market

CONFIG = {"digests": {"cards": ["news", "crowd"], "at": ["08:45"]},
          "feeds": {"intervals_minutes": {"news": 15}}}


def make_db(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    return path


def test_empty_database(tmp_path):
    body = market.market_payload(make_db(tmp_path), CONFIG)
    assert [c["kind"] for c in body["cards"]] == ["news", "crowd"]
    assert body["cards"][0]["content"] is None
    assert body["feeds"] == [] and body["crowd"] == []
    assert body["usage_24h"]["calls"] == 0


def test_cards_show_last_good_and_last_error(tmp_path):
    path = make_db(tmp_path)
    dstore.insert_digest(path, "news", "**Good.**", model="haiku")
    dstore.insert_digest(path, "news", "", ok=False, error="rate limited")
    card = market.market_payload(path, CONFIG)["cards"][0]
    assert card["content"] == "**Good.**" and card["last_error"] == "rate limited"
    assert card["age_minutes"] == 0


def test_feed_states_and_crowd(tmp_path):
    path = make_db(tmp_path)
    fstore.record_feed_run(path, "news", 5)
    fstore.record_feed_run(path, "filings", None, error="no SEC contact")
    fstore.insert_reddit_mentions(path, [{"filter": "all-stocks", "ticker": "NVDA", "name": "NVIDIA",
                                          "rank": 1, "mentions": 300, "upvotes": 1,
                                          "rank_24h_ago": 2, "mentions_24h_ago": 200}])
    dstore.log_llm_usage(path, "digest:news", model="haiku", usage={"input_tokens": 1000,
                         "output_tokens": 100}, cost_usd=0.01, duration_ms=1, ok=True)
    body = market.market_payload(path, CONFIG)
    states = {f["feed"]: f["state"] for f in body["feeds"]}
    assert states == {"news": "ok", "filings": "error"}
    assert body["crowd"][0]["change_pct"] == 50
    assert body["usage_24h"] == {"calls": 1, "tokens_in": 1000, "tokens_out": 100, "failed": 0}
