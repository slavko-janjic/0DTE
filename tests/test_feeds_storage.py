import json
from datetime import datetime, timedelta, timezone

from storage import db as storage
from storage import feeds


def make_db(tmp_path) -> str:
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    return path


def iso(hours_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def post(pid, **kw):
    base = {"id": pid, "subreddit": "wallstreetbets", "title": f"post {pid}",
            "created_at": iso(1), "tickers": ["TSLA"], "score": 10, "num_comments": 2,
            "tone": 0.5}
    base.update(kw)
    return base


def test_init_db_creates_feed_tables(tmp_path):
    path = make_db(tmp_path)
    assert feeds.feed_status(path) == {}
    assert feeds.symbol_universe(path) == set()


def test_reddit_upsert_counts_new_and_refreshes_score(tmp_path):
    path = make_db(tmp_path)
    assert feeds.upsert_reddit_posts(path, [post("a"), post("b")]) == 2
    assert feeds.upsert_reddit_posts(path, [post("a", score=99), post("c")]) == 1
    rows = {r["id"]: r for r in feeds.recent_reddit_posts(path)}
    assert rows["a"]["score"] == 99
    assert len(rows) == 3


def test_reddit_rss_refresh_keeps_existing_score(tmp_path):
    path = make_db(tmp_path)
    feeds.upsert_reddit_posts(path, [post("a", score=50)])
    feeds.upsert_reddit_posts(path, [post("a", score=None, num_comments=None)])
    assert feeds.recent_reddit_posts(path)[0]["score"] == 50


def test_recent_reddit_respects_window(tmp_path):
    path = make_db(tmp_path)
    feeds.upsert_reddit_posts(path, [post("old", created_at=iso(48)), post("new")])
    assert [r["id"] for r in feeds.recent_reddit_posts(path, hours=24)] == ["new"]


def test_news_dedupes_and_merges_tickers(tmp_path):
    path = make_db(tmp_path)
    item = {"id": "g1", "source": "yahoo", "title": "Nvidia beats", "published_at": iso(1),
            "tickers": ["NVDA"]}
    assert feeds.insert_news_items(path, [item]) == 1
    assert feeds.insert_news_items(path, [{**item, "tickers": ["QQQ"]}]) == 0
    row = feeds.recent_news_items(path)[0]
    assert json.loads(row["tickers_json"]) == ["NVDA", "QQQ"]


def test_filings_insert_or_ignore(tmp_path):
    path = make_db(tmp_path)
    today = datetime.now(timezone.utc).date().isoformat()
    f = {"accession": "0001-1", "ticker": "TSLA", "cik": 1318605, "form": "8-K",
         "filed_on": today, "link": "https://sec.gov/x"}
    assert feeds.insert_filings(path, [f, f]) == 1
    assert feeds.insert_filings(path, [f]) == 0
    assert feeds.recent_filings(path)[0]["form"] == "8-K"


def test_symbols_replace_and_lookup(tmp_path):
    path = make_db(tmp_path)
    feeds.replace_symbols(path, [{"ticker": "tsla", "cik": 1318605, "name": "Tesla"}])
    assert feeds.symbol_universe(path) == {"TSLA"}
    assert feeds.cik_for(path, "TSLA") == 1318605
    feeds.replace_symbols(path, [{"ticker": "NVDA", "cik": 1045810, "name": "NVIDIA"}])
    assert feeds.symbol_universe(path) == {"NVDA"}
    assert feeds.cik_for(path, "TSLA") is None


def test_ticker_mentions_combines_reddit_and_news(tmp_path):
    path = make_db(tmp_path)
    feeds.upsert_reddit_posts(path, [post("a", score=10, tone=1.0),
                                     post("b", score=5, tone=0.0)])
    feeds.insert_news_items(path, [{"id": "n", "source": "s", "title": "t",
                                    "published_at": iso(1), "tickers": ["TSLA", "NVDA"]}])
    m = feeds.ticker_mentions(path)
    assert m["TSLA"] == {"reddit_posts": 2, "reddit_score": 15, "news": 1, "reddit_tone": 0.5}
    assert m["NVDA"]["reddit_tone"] is None


def test_feed_status_keeps_last_ok_through_errors(tmp_path):
    path = make_db(tmp_path)
    feeds.record_feed_run(path, "news", 3)
    ok = feeds.feed_status(path)["news"]["last_ok"]
    feeds.record_feed_run(path, "news", None, error="timeout")
    st = feeds.feed_status(path)["news"]
    assert st["last_error"] == "timeout" and st["last_ok"] == ok
