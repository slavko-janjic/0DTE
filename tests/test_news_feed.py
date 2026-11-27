import feedparser

from data import news

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Nvidia and AMD rally as chip stocks rebound</title>
<link>https://x.com/a</link><guid>g-1</guid>
<pubDate>Thu, 26 Nov 2026 11:04:02 +0000</pubDate>
<description>&lt;p&gt;NVDA up 3%&lt;/p&gt;</description></item>
<item><title>Fed minutes due</title><link>https://x.com/b</link></item>
<item><title></title><link>https://x.com/empty</link></item>
</channel></rss>"""

UNIVERSE = {"NVDA", "AMD"}


def test_parse_general_feed_finds_tickers_and_times():
    items = news.parse_feed(feedparser.parse(RSS), "cnbc", UNIVERSE, set())
    assert [i["id"] for i in items] == ["g-1", "https://x.com/b"]
    first = items[0]
    assert first["tickers"] == ["AMD", "NVDA"]   # "Nvidia" is not a ticker; NVDA in the summary is
    assert first["summary"] == "NVDA up 3%"
    assert first["published_at"].startswith("2026-11-26T11:04:02")
    assert items[1]["published_at"] is None and items[1]["tickers"] == []


def test_per_ticker_feed_tags_every_item():
    items = news.parse_feed(feedparser.parse(RSS), "yahoo_QQQ", UNIVERSE, {"QQQ"}, tag="QQQ")
    assert all("QQQ" in i["tickers"] for i in items)


def test_feed_list_adds_yahoo_per_watchlist_ticker():
    cfg = {"feeds": {"watchlist": ["QQQ", "TSLA"], "news": {"feeds": {"a": "https://a"}}}}
    feeds = news.feed_list(cfg)
    assert feeds[0] == ("a", "https://a", None)
    assert ("yahoo_TSLA", news.YAHOO_TICKER_FEED.format(ticker="TSLA"), "TSLA") in feeds
    cfg["feeds"]["news"]["per_ticker_yahoo"] = False
    assert len(news.feed_list(cfg)) == 1


def test_collect_keeps_going_after_a_broken_feed(monkeypatch):
    import requests

    class Resp:
        content = RSS
        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        if "bad" in url:
            raise requests.ConnectionError("down")
        return Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    cfg = {"feeds": {"watchlist": [], "news": {"feeds": {"bad": "https://bad", "ok": "https://ok"}}}}
    items, errors = news.collect(cfg, UNIVERSE)
    assert len(items) == 2 and errors == ["bad: down"]
