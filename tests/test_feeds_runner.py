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
    (tmp_path / "secrets.env").write_text("# c\nSEC_USER_AGENT='abc'\nOTHER=1\n")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("OTHER", "keep")
    feeds.load_secrets(str(tmp_path / "t.db"))
    assert os.environ["SEC_USER_AGENT"] == "abc"
    assert os.environ["OTHER"] == "keep"


def test_run_due_runs_only_due_feeds_and_records_errors(tmp_path, monkeypatch):
    path = make_db(tmp_path)
    monkeypatch.setattr(feeds, "refresh_symbols_if_stale", lambda c, p: None)
    monkeypatch.setattr(feeds.news, "collect", lambda c, u: (
        [{"id": "1", "source": "s", "title": "t", "tickers": []}], ["bad: down"]))
    def boom(c):
        raise RuntimeError("reddit 429")
    monkeypatch.setattr(feeds.apewisdom, "collect", boom)
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


def test_failed_feed_retries_sooner():
    now = datetime.now(timezone.utc)
    failed = {"filings": {"last_run": (now - timedelta(minutes=11)).isoformat(),
                          "last_error": "no SEC contact"}}
    assert feeds.due(failed, "filings", 360, now)
    failed["filings"]["last_error"] = None
    assert not feeds.due(failed, "filings", 360, now)


# --- digest schedule ----------------------------------------------------------

NY = "America/New_York"
CFG = {"market_hours": {"timezone": NY}, "market_holidays": ["2026-12-25"],
       "digests": {"at": ["08:45", "11:00"]}}


def ny(y, m, d, hh, mm):
    from zoneinfo import ZoneInfo
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(NY)).astimezone(timezone.utc)


def test_digest_slot_latest_passed_only():
    now = ny(2026, 11, 30, 12, 0)                 # Monday noon
    slot = feeds.digest_slot_due(CFG, None, now)
    assert slot == ny(2026, 11, 30, 11, 0)
    assert feeds.digest_slot_due(CFG, ny(2026, 11, 30, 11, 1).isoformat(), now) is None
    assert feeds.digest_slot_due(CFG, ny(2026, 11, 30, 9, 0).isoformat(), now) == slot


def test_digest_slot_none_before_first_weekend_holiday_or_disabled():
    assert feeds.digest_slot_due(CFG, None, ny(2026, 11, 30, 8, 0)) is None
    assert feeds.digest_slot_due(CFG, None, ny(2026, 11, 28, 12, 0)) is None   # Saturday
    assert feeds.digest_slot_due(CFG, None, ny(2026, 12, 25, 12, 0)) is None   # holiday
    off = {**CFG, "digests": {"enabled": False}}
    assert feeds.digest_slot_due(off, None, ny(2026, 11, 30, 12, 0)) is None
