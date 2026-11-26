"""Reddit collector: recent posts from the configured subreddits.

Uses Reddit's API through PRAW when credentials are set in the environment
(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET; a free 'script' app made at
https://www.reddit.com/prefs/apps). Without them it falls back to each
subreddit's public RSS feed - no scores or comment counts, and Reddit
rate-limits it hard, so the API is the real path.

Stores raw posts only. `tone` is a crude keyword count (calls/moon vs
puts/crash) so the dashboard has something before the LLM digests exist; the
digests do the real reading.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from sentiment.tickers import extract_tickers

USER_AGENT = "windows:0dte-research:v0.1 (personal research tool)"
BODY_MAX_CHARS = 2000

_BULL = {"call", "calls", "moon", "mooning", "rocket", "bull", "bullish", "long",
         "buy", "buying", "squeeze", "rip", "ripping", "breakout", "tendies", "pump"}
_BEAR = {"put", "puts", "crash", "crashing", "bear", "bearish", "short", "shorting",
         "sell", "selling", "dump", "dumping", "drill", "drilling", "rug", "bagholder",
         "bagholding", "recession", "red"}
_WORD = re.compile(r"[a-z]+")


def tone(text: str | None) -> float | None:
    """(bullish words - bearish words) / both, in [-1, 1]; None when neither."""
    if not text:
        return None
    words = _WORD.findall(text.lower())
    bull = sum(w in _BULL for w in words)
    bear = sum(w in _BEAR for w in words)
    if bull + bear == 0:
        return None
    return round((bull - bear) / (bull + bear), 3)


def _post_dict(*, pid, subreddit, title, body, author, url, score, num_comments,
               created_utc, universe, always) -> dict:
    text = f"{title}\n{body or ''}"
    return {
        "id": pid,
        "subreddit": subreddit,
        "title": title,
        "body": (body or "")[:BODY_MAX_CHARS] or None,
        "author": author,
        "url": url,
        "score": score,
        "num_comments": num_comments,
        "created_at": datetime.fromtimestamp(created_utc, timezone.utc).isoformat(),
        "tickers": extract_tickers(text, universe, always),
        "tone": tone(text),
    }


def has_api_credentials() -> bool:
    return bool(os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"))


def fetch_api(subreddits: list[str], per_listing: int, universe: set[str],
              always: set[str]) -> list[dict]:
    """Hot + new from each subreddit via PRAW (read-only, app-only auth)."""
    import praw  # imported here so the rest works without it installed
    reddit = praw.Reddit(client_id=os.environ["REDDIT_CLIENT_ID"],
                         client_secret=os.environ["REDDIT_CLIENT_SECRET"],
                         user_agent=USER_AGENT, check_for_async=False)
    reddit.read_only = True
    seen: dict[str, dict] = {}
    for name in subreddits:
        sub = reddit.subreddit(name)
        for listing in (sub.hot(limit=per_listing), sub.new(limit=per_listing)):
            for s in listing:
                if s.stickied or s.id in seen:
                    continue
                seen[s.id] = _post_dict(
                    pid=s.id, subreddit=name, title=s.title, body=s.selftext,
                    author=str(s.author) if s.author else None,
                    url=f"https://www.reddit.com{s.permalink}", score=s.score,
                    num_comments=s.num_comments, created_utc=s.created_utc,
                    universe=universe, always=always)
    return list(seen.values())


def parse_rss(parsed, subreddit: str, universe: set[str], always: set[str]) -> list[dict]:
    """Posts from a feedparser result of https://www.reddit.com/r/<sub>/new/.rss"""
    import calendar
    out = []
    for e in parsed.entries:
        pid = (e.get("id") or "").rsplit("/", 1)[-1].removeprefix("t3_")
        stamp = e.get("updated_parsed") or e.get("published_parsed")
        if not pid or not stamp:
            continue
        body = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        body = re.sub(r"\s+", " ", body).strip()
        author = (e.get("author") or "").removeprefix("/u/") or None
        out.append(_post_dict(pid=pid, subreddit=subreddit, title=e.get("title", ""),
                              body=body, author=author, url=e.get("link"), score=None,
                              num_comments=None, created_utc=calendar.timegm(stamp),
                              universe=universe, always=always))
    return out


def fetch_rss(subreddits: list[str], universe: set[str], always: set[str],
              timeout: float = 15) -> list[dict]:
    import feedparser
    import requests
    out = []
    for name in subreddits:
        resp = requests.get(f"https://www.reddit.com/r/{name}/new/.rss",
                            headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        out.extend(parse_rss(feedparser.parse(resp.content), name, universe, always))
    return out


def collect(config: dict, universe: set[str]) -> list[dict]:
    cfg = config.get("feeds", {}).get("reddit", {})
    subs = cfg.get("subreddits", ["wallstreetbets", "options", "stocks", "investing"])
    always = set(config.get("feeds", {}).get("watchlist", []))
    if has_api_credentials():
        return fetch_api(subs, cfg.get("per_listing", 50), universe, always)
    return fetch_rss(subs, universe, always)
