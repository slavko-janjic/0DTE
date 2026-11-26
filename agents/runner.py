"""One wake-up of one LLM agent: build its briefing, give it the desk tools,
let it think and act, and record the run.

Runs through the Claude Agent SDK on the Claude subscription of the Claude
Code CLI on this PC. Built-in tools are limited to WebSearch (capped per
wake-up by a hook); everything else is the desk server. No file, shell or
code tools, and nothing from this repo's settings is loaded.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime, timezone

from agents import prompts
from agents.desk import Desk
from agents.tools import build_server
from broker import service
from storage import agent_journal as journal
from storage import digests as dstore

log = logging.getLogger("agents")
DEFAULT_MODEL = "sonnet"
DEFAULT_MAX_TURNS = 30
DEFAULT_TIMEOUT_S = 600


async def _drive(desk: Desk, system: str, prompt: str, model: str, max_turns: int, timeout: float):
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
                                  TextBlock, query)
    server, tool_names = build_server(desk)

    async def websearch_gate(input_data, tool_use_id, context):
        if desk.use_deep_dive():
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "No web searches left this wake-up."}}

    with tempfile.TemporaryDirectory(prefix="0dte-agent-") as empty:
        options = ClaudeAgentOptions(
            model=model, system_prompt=system, tools=["WebSearch"],
            allowed_tools=tool_names + ["WebSearch"], mcp_servers={"desk": server},
            max_turns=max_turns, setting_sources=[], cwd=empty,
            hooks={"PreToolUse": [HookMatcher(matcher="WebSearch", hooks=[websearch_gate])]})
        texts, result = [], None

        async def go():
            nonlocal result
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    texts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    result = msg

        await asyncio.wait_for(go(), timeout)
    return result, texts


def run_agent(db_path: str, config: dict, agent_cfg: dict, mode: str | None = None, market=None,
              drive=_drive) -> dict:
    """Run one wake-up. mode: 'live' | 'dry_run' (default: the agent's config).
    Never raises; returns the run row's fields."""
    name = agent_cfg["name"]
    mode = mode or agent_cfg.get("mode", "dry_run")
    model = agent_cfg.get("model", DEFAULT_MODEL)
    run_id = journal.start_run(db_path, name, mode)
    market = market or service.market_state(config)
    desk = Desk(db_path, config, agent_cfg, run_id, mode, market=market)
    status, summary, error, res = "ok", None, None, None
    try:
        portfolio = desk.portfolio()
        system = prompts.system_prompt(config, agent_cfg)
        prompt = prompts.briefing(db_path, config, agent_cfg, portfolio, market, mode)
        res, texts = asyncio.run(drive(desk, system, prompt, model,
                                       agent_cfg.get("max_turns", DEFAULT_MAX_TURNS),
                                       agent_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_S)))
        if res is None:
            status, error = "error", "no result from the model"
        elif res.is_error:
            status, error = "error", str(res.result or res.subtype)[:500]
        final = (res.result if res is not None and res.result else (texts[-1] if texts else "")) or ""
        summary = final.strip().splitlines()[-1][:500] if final.strip() else None
    except asyncio.TimeoutError:
        status, error = "timeout", "wake-up ran past its time limit"
    except Exception as exc:  # noqa: BLE001
        status, error = "error", f"{type(exc).__name__}: {exc}"[:500]
    usage = dict(res.usage or {}) if res is not None else {}
    tokens_in = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                "cache_creation_input_tokens"))
    dstore.log_llm_usage(db_path, f"agent:{name}", model=model, usage=usage,
                         cost_usd=res.total_cost_usd if res is not None else None,
                         duration_ms=res.duration_ms if res is not None else None,
                         ok=status == "ok", error=error)
    journal.finish_run(db_path, run_id, status, summary=summary, orders=desk.orders_placed,
                       turns=res.num_turns if res is not None else None, tokens_in=tokens_in,
                       tokens_out=usage.get("output_tokens"),
                       cost_usd=res.total_cost_usd if res is not None else None, error=error)
    log.info("%s (%s): %s, %d order(s), %s tokens in / %s out%s", name, mode, status,
             desk.orders_placed, tokens_in, usage.get("output_tokens"),
             f" - {summary}" if summary else (f" - {error}" if error else ""))
    return {"run_id": run_id, "status": status, "summary": summary, "orders": desk.orders_placed,
            "tokens_in": tokens_in, "tokens_out": usage.get("output_tokens"), "error": error}
