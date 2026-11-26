import feedparser

from sentiment import reddit
from sentiment.tickers import extract_tickers

UNIVERSE = {"TSLA", "NVDA", "AMD", "IT", "ALL", "F", "DD", "GME"}


def test_cashtags_and_bare_tickers():
    text = "Loading $tsla calls and NVDA puts, $F too. AMD!"
    assert extract_tickers(text, UNIVERSE) == ["AMD", "F", "NVDA", "TSLA"]


def test_stopwords_and_single_letters_need_cashtag():
    text = "IT is ALL about the DD, not F"
    assert extract_tickers(text, UNIVERSE) == []
    assert extract_tickers("$IT and $DD", UNIVERSE) == ["DD", "IT"]


def test_unknown_words_ignored_and_watchlist_always_counts():
    assert extract_tickers("HELLO WORLD $ZZZZ", UNIVERSE) == []
    assert extract_tickers("SPY and QQQ", UNIVERSE, always={"SPY", "QQQ"}) == ["QQQ", "SPY"]


def test_lowercase_bare_words_do_not_count():
    assert extract_tickers("gme to the moon", UNIVERSE) == []


def test_tone():
    assert reddit.tone("calls calls moon") == 1.0
    assert reddit.tone("puts and a crash") == -1.0
    assert reddit.tone("buy calls but crash") == round((2 - 1) / 3, 3)
    assert reddit.tone("nothing here") is None


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
  <author><name>/u/apes</name></author>
  <id>t3_abc123</id>
  <link href="https://www.reddit.com/r/wallstreetbets/comments/abc123/x/"/>
  <updated>2026-11-26T14:00:00+00:00</updated>
  <title>$TSLA calls printing</title>
  <content type="html">&lt;p&gt;NVDA next, moon&lt;/p&gt;</content>
</entry>
<entry>
  <id>t3_nostamp</id>
  <title>no date</title>
</entry>
</feed>"""


def test_parse_rss():
    posts = reddit.parse_rss(feedparser.parse(RSS), "wallstreetbets", UNIVERSE, set())
    assert len(posts) == 1
    p = posts[0]
    assert p["id"] == "abc123"
    assert p["author"] == "apes"
    assert p["tickers"] == ["NVDA", "TSLA"]
    assert p["score"] is None
    assert p["created_at"].startswith("2026-11-26T14:00:00")
    assert p["tone"] == 1.0


def test_collect_uses_rss_without_credentials(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    called = {}
    monkeypatch.setattr(reddit, "fetch_rss", lambda subs, u, a: called.setdefault("rss", subs) and [])
    reddit.collect({"feeds": {"reddit": {"subreddits": ["options"]}}}, UNIVERSE)
    assert called["rss"] == ["options"]
