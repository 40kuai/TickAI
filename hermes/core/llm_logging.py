"""
LLM call logging utilities.

Structured logging for LLM requests, responses, and tool calls.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

_LOG_BAR = "─" * 60
_LOG_CONTENT_PREVIEW = 200
_LOG_PAYLOAD_PREVIEW = 4000


def _now_hms() -> str:
    return time.strftime("%H:%M:%S")


def _summarize_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for call in tool_calls:
        cid = call.get("id", "?")
        fn = call.get("function", {})
        name = fn.get("name", "?")
        args = fn.get("arguments", "")
        try:
            args_obj = json.loads(args) if isinstance(args, str) else args
            args_str = json.dumps(args_obj, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            args_str = str(args)
        lines.append(f"  → {cid} | {name}({args_str})")
    return lines


def log_request(payload: Dict[str, Any], endpoint: str, model: str) -> None:
    """Log an LLM request."""
    messages = payload.get("messages", [])
    tools = payload.get("tools") or []

    lines = [
        "",
        _LOG_BAR,
        f"[LLM CALL] {_now_hms()}",
        f"endpoint: {endpoint}",
        f"model: {model}",
        f"messages: {len(messages)}",
    ]
    if tools:
        lines.append(f"tools: {len(tools)}")
    lines.append(_LOG_BAR)
    print("\n".join(lines), flush=True)


def log_response(resp: Dict[str, Any], elapsed_ms: int, status_code: int) -> None:
    """Log an LLM response."""
    choices = resp.get("choices") or []
    first = choices[0] if choices else {}
    msg = first.get("message") or {}
    finish_reason = first.get("finish_reason", "?")
    tool_calls = msg.get("tool_calls") or []
    usage = resp.get("usage") or {}

    lines = [
        "",
        _LOG_BAR,
        f"[LLM RESP] {_now_hms()} (latency={elapsed_ms}ms, status={status_code})",
        f"finish_reason: {finish_reason}",
    ]
    if usage:
        lines.append(
            f"tokens: prompt={usage.get('prompt_tokens', '?')} "
            f"completion={usage.get('completion_tokens', '?')} "
            f"total={usage.get('total_tokens', '?')}"
        )
    if tool_calls:
        lines.append(f"tool_calls: {len(tool_calls)}")
        lines.extend(_summarize_tool_calls(tool_calls))
    lines.append(_LOG_BAR)
    print("\n".join(lines), flush=True)


def log_error(exc: Exception, elapsed_ms: int) -> None:
    """Log an LLM error."""
    print(
        f"\n{_LOG_BAR}\n[LLM ERROR] {_now_hms()} (latency={elapsed_ms}ms)\n"
        f"{type(exc).__name__}: {exc}\n{_LOG_BAR}\n",
        flush=True,
    )


def log_tool_call(name: str, args: Dict[str, Any], result: str,
                   elapsed_ms: int) -> None:
    """Log a tool execution."""
    result_preview = result[:_LOG_CONTENT_PREVIEW]
    truncated = "..." if len(result) > _LOG_CONTENT_PREVIEW else ""
    print(
        f"  [TOOL] {name}({json.dumps(args, ensure_ascii=False)}) "
        f"→ {elapsed_ms}ms\n"
        f"  [→] {result_preview}{truncated}",
        flush=True,
    )
