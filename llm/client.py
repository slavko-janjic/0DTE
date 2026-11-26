"""One-shot model calls through the Claude Agent SDK.

The SDK drives the Claude Code CLI installed on this PC, so calls run on the
Claude subscription that CLI is logged in with (no API key). Each call here is
a single turn with no tools, run from an empty folder with no settings loaded,
so nothing from this repo (CLAUDE.md, .claude/) leaks into the prompt or the
bill. Every call is logged to llm_usage, success or not.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass

from storage import digests as store


class LlmError(RuntimeError):
    pass


@dataclass
class LlmResult:
    text: str
    model: str
    usage: dict
    cost_usd: float | None
    duration_ms: int


async def _query(prompt: str, system: str, model: str, timeout: float) -> LlmResult:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    with tempfile.TemporaryDirectory(prefix="0dte-llm-") as empty:
        options = ClaudeAgentOptions(
            model=model, system_prompt=system, tools=[], allowed_tools=[], max_turns=1,
            setting_sources=[], cwd=empty)
        parts, result = [], None
        started = time.monotonic()

        async def run():
            nonlocal result
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    parts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    result = msg

        await asyncio.wait_for(run(), timeout)
    if result is None:
        raise LlmError("no result from the model")
    if result.is_error:
        raise LlmError(f"model call failed: {result.result or result.subtype}")
    text = (result.result or "".join(parts)).strip()
    return LlmResult(text=text, model=model, usage=dict(result.usage or {}),
                     cost_usd=result.total_cost_usd,
                     duration_ms=result.duration_ms or int((time.monotonic() - started) * 1000))


def complete(prompt: str, *, system: str, model: str, purpose: str, db_path: str,
             timeout: float = 180) -> LlmResult:
    """Run one prompt and return the text; logs usage either way. Raises
    LlmError (or the SDK's own error, e.g. CLI not found) on failure."""
    try:
        res = asyncio.run(_query(prompt, system, model, timeout))
    except Exception as exc:
        store.log_llm_usage(db_path, purpose, model=model, usage=None, cost_usd=None,
                            duration_ms=None, ok=False, error=str(exc)[:500])
        raise
    store.log_llm_usage(db_path, purpose, model=model, usage=res.usage, cost_usd=res.cost_usd,
                        duration_ms=res.duration_ms, ok=True)
    return res
