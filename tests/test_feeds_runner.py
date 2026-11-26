import os
from datetime import datetime, timedelta, timezone

import feeds
from storage import db as storage
from storage import feeds as feed_store


def make_db(tmp_path):
    path = str(tmp_path / "t.db")
    storage.init_db(path)
    return path


def test_due():
    now = datetime.now(timezone.utc)
    recent = {"news": {"last_run": (now - timedelta(minutes=5)).isoformat()}}
    assert feeds.due({}, "news", 15, now)
    assert not feeds.due(recent, "news", 15, now)
    assert feeds.due(recent, "news", 5, now)


def test_load_secrets_does_not_override(tmp_path, monkeypatch):
    (tmp_path / "secrets.env").write_text("# c\nREDDIT_CLIENT_ID='abc'\nOTHER=1\n")
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.setenv("OTHER", "keep")
    feeds.load_secrets(str(tmp_path / "t.db"))
    assert os.environ["REDDIT_CLIENT_ID"] == "abc"
    assert os.environ["OTHER"] == "keep"


def test_run_due_runs_only_due_feeds_and_records_errors(tmp_path, monkeypatch):
    path = make_db(tmp_path)
    monkeypatch.setattr(feeds, "refresh_symbols_if_stale", lambda c, p: None)
    monkeypatch.setattr(feeds.news, "collect", lambda c, u: (
        [{"id": "1", "source": "s", "title": "t", "tickers": []}], ["bad: down"]))
    def boom(c, u):
        raise RuntimeError("reddit 429")
    monkeypatch.setattr(feeds.reddit, "collect", boom)
    feed_store.record_feed_run(path, "filings", 0)          # just ran: not due
    cfg = {"feeds": {"intervals_minutes": {"reddit": 30, "news": 15, "filings": 360}}}
    feeds.run_due(cfg, path)
    st = feed_store.feed_status(path)
    assert st["news"]["items_new"] == 1 and st["news"]["last_error"] == "bad: down"
    assert st["reddit"]["last_error"] == "reddit 429" and st["reddit"]["last_ok"] is None
    assert st["filings"]["items_new"] == 0


def test_disabled_feeds_do_nothing(tmp_path, monkeypatch):
    path = make_db(tmp_path)
    monkeypatch.setattr(feeds, "refresh_symbols_if_stale",
                        lambda c, p: (_ for _ in ()).throw(AssertionError("ran")))
    feeds.run_due({"feeds": {"enabled": False}}, path)


def test_load_secrets_handles_bom_and_txt_extension(tmp_path, monkeypatch):
    (tmp_path / "secrets.env.txt").write_text("SEC_USER_AGENT=x a@b.c\n", encoding="utf-8-sig")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    feeds.load_secrets(str(tmp_path / "t.db"))
    assert os.environ["SEC_USER_AGENT"] == "x a@b.c"


def test_load_secrets_reads_utf16(tmp_path, monkeypatch):
    (tmp_path / "secrets.env").write_text("SEC_USER_AGENT=x a@b.c\n", encoding="utf-16")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    feeds.load_secrets(str(tmp_path / "t.db"))
    assert os.environ["SEC_USER_AGENT"] == "x a@b.c"
