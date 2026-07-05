"""Combines per-category subscores into one composite signal: direction,
confidence %, and a plain-language recommendation.

Categories with no data (None) are dropped and the remaining weights are
renormalized, so a flaky source degrades the signal instead of breaking it.
"""
from dataclasses import dataclass


@dataclass
class CompositeSignal:
    ticker: str
    composite_score: float          # -1..1
    direction: str                  # 'bullish' | 'bearish' | 'neutral'
    confidence_pct: float           # 0..100
    recommendation: str
    subscores_used: dict[str, float]


def compute_composite_score(subscores: dict[str, float | None], weights: dict[str, float]) -> float | None:
    available = {k: v for k, v in subscores.items() if v is not None}
    if not available:
        return None
    total_weight = sum(weights.get(k, 0.0) for k in available)
    if total_weight == 0:
        return None
    return sum(v * weights.get(k, 0.0) for k, v in available.items()) / total_weight


def direction_from_score(score: float, neutral_epsilon: float = 0.02) -> str:
    if score > neutral_epsilon:
        return "bullish"
    if score < -neutral_epsilon:
        return "bearish"
    return "neutral"


def build_recommendation(
    ticker: str, direction: str, confidence_pct: float, confidence_floor_pct: float
) -> str:
    if confidence_pct < confidence_floor_pct or direction == "neutral":
        return f"No clear edge on {ticker} - stay out."
    option_type = "calls" if direction == "bullish" else "puts"
    move = "up" if direction == "bullish" else "down"
    return (
        f"{confidence_pct:.0f}% confidence {ticker} moves {move} - "
        f"consider buying 0DTE {option_type}."
    )


def compute_signal(
    ticker: str,
    subscores: dict[str, float | None],
    weights: dict[str, float],
    confidence_floor_pct: float,
) -> CompositeSignal | None:
    score = compute_composite_score(subscores, weights)
    if score is None:
        return None
    direction = direction_from_score(score)
    confidence_pct = abs(score) * 100.0
    recommendation = build_recommendation(ticker, direction, confidence_pct, confidence_floor_pct)
    subscores_used = {k: v for k, v in subscores.items() if v is not None}
    return CompositeSignal(
        ticker=ticker, composite_score=score, direction=direction,
        confidence_pct=confidence_pct, recommendation=recommendation,
        subscores_used=subscores_used,
    )
