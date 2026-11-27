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


def mention(ticker, filter_name="all-stocks", **kw):
    base = {"filter": filter_name, "ticker": ticker, "name": ticker, "rank": 1,
            "mentions": 100, "upvotes": 500, "rank_24h_ago": 3, "mentions_24h_ago": 40}
    base.update(kw)
    return base


def test_init_db_creates_feed_tables(tmp_path):
    path = make_db(tmp_path)
    assert feeds.feed_status(path) == {}
    assert feeds.symbol_universe(path) == set()


def test_reddit_mentions_latest_snapshot_only(tmp_path):
    path = make_db(tmp_path)
    feeds.insert_reddit_mentions(path, [mention("NVDA", rank=1), mention("TSLA", rank=2)],
                                 taken_at=iso(2))
    assert feeds.insert_reddit_mentions(path, [mention("TSLA", rank=1, mentions=300)],
                                        taken_at=iso(1)) == 1
    latest = feeds.latest_reddit_mentions(path)
    assert [(r["ticker"], r["mentions"]) for r in latest] == [("TSLA", 300)]
    assert feeds.latest_reddit_mentions(path, "options") == []


def test_reddit_mention_history(tmp_path):
    path = make_db(tmp_path)
    feeds.insert_reddit_mentions(path, [mention("TSLA", mentions=10)], taken_at=iso(60))
    feeds.insert_reddit_mentions(path, [mention("TSLA", mentions=20)], taken_at=iso(2))
    feeds.insert_reddit_mentions(path, [mention("TSLA", mentions=30)], taken_at=iso(1))
    assert [r["mentions"] for r in feeds.reddit_mention_history(path, "tsla")] == [20, 30]


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
    feeds.insert_reddit_mentions(path, [mention("TSLA", rank=4, mentions=80, upvotes=900,
                                                mentions_24h_ago=20)])
    feeds.insert_news_items(path, [{"id": "n", "source": "s", "title": "t",
                                    "published_at": iso(1), "tickers": ["TSLA", "NVDA"]}])
    m = feeds.ticker_mentions(path)
    assert m["TSLA"] == {"reddit_mentions": 80, "reddit_mentions_24h_ago": 20,
                         "reddit_upvotes": 900, "reddit_rank": 4, "news": 1}
    assert m["NVDA"]["reddit_mentions"] == 0 and m["NVDA"]["news"] == 1


def test_feed_status_keeps_last_ok_through_errors(tmp_path):
    path = make_db(tmp_path)
    feeds.record_feed_run(path, "news", 3)
    ok = feeds.feed_status(path)["news"]["last_ok"]
    feeds.record_feed_run(path, "news", None, error="timeout")
    st = feeds.feed_status(path)["news"]
    assert st["last_error"] == "timeout" and st["last_ok"] == ok
