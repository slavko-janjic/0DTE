"""Turn each card's input data into a short digest with a small model, and
store it. The digests are what the LLM agents read instead of raw feeds - one
build serves every agent.
"""
from __future__ import annotations

import logging

from digests import inputs
from llm import client
from storage import digests as store

log = logging.getLogger("feeds")

DEFAULT_MODEL = "haiku"
MAX_INPUT_CHARS = 24000          # ~6k tokens; inputs are capped well below this anyway

COMMON = (
    "You write one card of a market briefing that trading agents and a human trader read "
    "before deciding anything. Use only the data given - never add facts, prices or news "
    "from memory, and never recommend trades. Be concrete: tickers, numbers, times. "
    "Format: Markdown. First line: '**' + a one-sentence headline + '**'. Then at most "
    "6 bullets ('- '), each one line. If the data shows nothing notable, say so in one bullet. "
    "Under 160 words."
)

PROMPTS = {
    "crowd": "Card: Reddit crowd. What retail is piling into: leaders, the sharpest rises or drops "
             "in mentions versus 24h earlier, anything new near the top, and how the watchlist "
             "tickers rank. Flag crowded trades (very high mentions, rising fast).",
    "news": "Card: news. The headlines most likely to move US stocks or indexes today, grouped "
            "by ticker or theme (macro, Fed, earnings, sector). Skip lifestyle, personal finance "
            "and duplicate stories.",
    "filings": "Card: SEC filings. Notable filings for the watchlist: 8-K events (say what the "
               "items mean), clusters of insider Form 4 reports by ticker, anything unusual. Note "
               "that a Form 4 alone does not say buy or sell.",
    "market": "Card: market state for QQQ and SPY. Spot, gap and pre-market setup, gamma regime, "
              "VIX level and term structure (contango normal, backwardation = stress), and the "
              "premarket range class when given (quiet / normal / wide: a forecast of how much the "
              "day moves vs option prices, not of direction). Keep the worker's caveat that its "
              "direction calls are close to a coin flip.",
    "calendar": "Card: calendar. Upcoming catalysts in date order with day names: macro releases, "
                "Fed, holidays and half days, watchlist earnings. Mark anything today or tomorrow.",
}


def build_card(kind: str, db_path: str, config: dict, complete=client.complete) -> dict:
    """Build and store one card. Returns {'kind', 'ok', 'content'}; never raises."""
    model = config.get("digests", {}).get("model", DEFAULT_MODEL)
    try:
        data = inputs.BUILDERS[kind](db_path, config)
    except Exception as exc:  # noqa: BLE001
        store.insert_digest(db_path, kind, "", ok=False, error=f"input: {exc}"[:500])
        log.warning("digest %s: input failed: %s", kind, exc)
        return {"kind": kind, "ok": False, "content": ""}
    if not data:
        content = "**No data yet.**\n- The feed for this card has nothing stored."
        store.insert_digest(db_path, kind, content, input_chars=0)
        return {"kind": kind, "ok": True, "content": content}
    data = data[:MAX_INPUT_CHARS]
    try:
        res = complete(f"{PROMPTS[kind]}\n\nDATA:\n{data}", system=COMMON, model=model,
                       purpose=f"digest:{kind}", db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - recorded; the previous good card stays on show
        store.insert_digest(db_path, kind, "", input_chars=len(data), model=model, ok=False,
                            error=str(exc)[:500])
        log.warning("digest %s: model failed: %s", kind, exc)
        return {"kind": kind, "ok": False, "content": ""}
    store.insert_digest(db_path, kind, res.text, input_chars=len(data), model=model)
    log.info("digest %s: built (%d chars in, %s tokens out)", kind, len(data),
             res.usage.get("output_tokens"))
    return {"kind": kind, "ok": True, "content": res.text}


def build_all(db_path: str, config: dict, complete=client.complete) -> list[dict]:
    kinds = config.get("digests", {}).get("cards", list(PROMPTS))
    return [build_card(k, db_path, config, complete) for k in kinds if k in PROMPTS]
