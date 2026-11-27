from sentiment import apewisdom
from sentiment.tickers import extract_tickers

PAGE = {"count": 2, "pages": 1, "current_page": 1, "results": [
    {"rank": 1, "ticker": "NVDA", "name": "NVIDIA", "mentions": 321, "upvotes": 15157,
     "rank_24h_ago": 1, "mentions_24h_ago": 563},
    {"rank": "2", "ticker": "spy", "name": "SPDR S&amp;P 500 ETF Trust", "mentions": "302",
     "upvotes": 715, "rank_24h_ago": None, "mentions_24h_ago": None},
    {"rank": 3, "ticker": "", "name": "blank"},
]}


def test_parse_page():
    rows = apewisdom.parse_page(PAGE, "wallstreetbets")
    assert [r["ticker"] for r in rows] == ["NVDA", "SPY"]
    spy = rows[1]
    assert spy["name"] == "SPDR S&P 500 ETF Trust"
    assert spy["rank"] == 2 and spy["mentions"] == 302 and spy["rank_24h_ago"] is None
    assert spy["filter"] == "wallstreetbets"


def test_collect_pages_and_errors(monkeypatch):
    import requests
    calls = []

    class Resp:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    def fake_get(url, **kw):
        calls.append(url)
        if "/options/" in url:
            raise requests.ConnectionError("down")
        return Resp({**PAGE, "pages": 3})

    monkeypatch.setattr(requests, "get", fake_get)
    cfg = {"feeds": {"reddit": {"filters": ["all-stocks", "options"], "pages": 2}}}
    rows, errors = apewisdom.collect(cfg)
    assert len([c for c in calls if "all-stocks" in c]) == 2
    assert len(rows) == 4 and errors == ["options: down"]


# --- ticker extraction (used by news) -----------------------------------------

UNIVERSE = {"TSLA", "NVDA", "AMD", "IT", "ALL", "F", "DD"}


def test_cashtags_and_bare_tickers():
    assert extract_tickers("Loading $tsla calls and NVDA puts, $F too. AMD!", UNIVERSE) == \
        ["AMD", "F", "NVDA", "TSLA"]


def test_stopwords_and_single_letters_need_cashtag():
    assert extract_tickers("IT is ALL about the DD, not F", UNIVERSE) == []
    assert extract_tickers("$IT and $DD", UNIVERSE) == ["DD", "IT"]


def test_watchlist_always_counts_and_lowercase_ignored():
    assert extract_tickers("SPY and QQQ", UNIVERSE, always={"SPY", "QQQ"}) == ["QQQ", "SPY"]
    assert extract_tickers("nvda moon", UNIVERSE) == []
