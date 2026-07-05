"""Combines sentiment sources into one -1..1 subscore, plus raw context.

ApeWisdom and StockTwits are averaged (equal weight between the two, renormalized
if one is unavailable). PRAW's raw post titles are returned separately as
context for the dashboard - they don't feed the numeric score in v1.

Grok/X is intentionally not implemented here yet - `sentiment_sources.grok_x`
in config is a reserved flag for a future pluggable backend.
"""
from dataclasses import dataclass, field

from sentiment import apewisdom, reddit_praw, stocktwits


@dataclass
class SentimentResult:
    score: float | None          # -1..1, None if no source returned data
    sources_used: list[str] = field(default_factory=list)
    raw_context: list[str] = field(default_factory=list)


def get_sentiment(ticker: str, config: dict) -> SentimentResult:
    sources_cfg = config.get("sentiment_sources", {})
    scores: dict[str, float] = {}

    if sources_cfg.get("apewisdom", True):
        score = apewisdom.get_mention_momentum(ticker)
        if score is not None:
            scores["apewisdom"] = score

    if sources_cfg.get("stocktwits", True):
        score = stocktwits.get_bull_bear_score(ticker)
        if score is not None:
            scores["stocktwits"] = score

    raw_context: list[str] = []
    if sources_cfg.get("reddit_praw", True):
        subreddits = config.get("reddit", {}).get("subreddits", ["options", "wallstreetbets"])
        raw_context = reddit_praw.get_recent_posts(ticker, subreddits)

    if not scores:
        return SentimentResult(score=None, sources_used=[], raw_context=raw_context)

    combined = sum(scores.values()) / len(scores)
    return SentimentResult(score=combined, sources_used=list(scores.keys()), raw_context=raw_context)
