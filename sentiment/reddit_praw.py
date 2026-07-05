"""PRAW client for raw Reddit post text - supporting context, not a numeric score.

Requires a free Reddit API app (script type) and these environment variables:
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
See SETUP.md for how to create one.
"""
import os

import praw


def _get_client() -> praw.Reddit | None:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "0dte-paper-trading-tool")
    if not client_id or not client_secret:
        return None
    try:
        return praw.Reddit(
            client_id=client_id, client_secret=client_secret, user_agent=user_agent,
            check_for_async=False,
        )
    except Exception:
        return None


def get_recent_posts(ticker: str, subreddits: list[str], limit_per_sub: int = 5) -> list[str]:
    """Returns recent post titles mentioning the ticker, across the given subreddits.

    Best-effort: returns an empty list on any failure (missing creds, network,
    rate limits) rather than raising.
    """
    reddit = _get_client()
    if reddit is None:
        return []

    titles: list[str] = []
    for sub_name in subreddits:
        try:
            subreddit = reddit.subreddit(sub_name)
            for submission in subreddit.search(ticker, sort="new", time_filter="day",
                                                 limit=limit_per_sub):
                titles.append(submission.title)
        except Exception:
            continue
    return titles
