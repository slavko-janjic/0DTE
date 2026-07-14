"""Combines sentiment sources into one -1..1 subscore, plus raw context.

ApeWisdom and StockTwits are averaged (equal weight, renormalized if either
is unavailable). Reddit's public search returns raw post titles separately
as context for the dashboard - it doesn't feed the numeric score, and is
currently blocked by Reddit's bot-protection more often than not (see
reddit_public.py) - kept as best-effort since that may change.

Grok/X is intentionally not implemented here yet - `sentiment_sources.grok_x`
in config is a reserved flag for a future pluggable backend.
"""
from dataclasses import dataclass, field

from sentiment import apewisdom, reddit_public, stocktwits


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
    if sources_cfg.get("reddit_context", True):
        subreddits = config.get("reddit", {}).get("subreddits", ["options", "wallstreetbets"])
        raw_context = reddit_public.get_recent_posts(ticker, subreddits)

    if not scores:
        return SentimentResult(score=None, sources_used=[], raw_context=raw_context)

    combined = sum(scores.values()) / len(scores)
    return SentimentResult(score=combined, sources_used=list(scores.keys()), raw_context=raw_context)
