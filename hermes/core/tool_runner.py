"""
Tool call orchestration.

Implements the multi-turn tool-calling loop for LLM agents.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .llm_client import TokenHubClient, AsyncTokenHubClient
from .llm_logging import log_tool_call
from ..tools.registry import registry

MAX_TOOL_ROUNDS = 5


def _tool_calls_from_response(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract tool_calls from a chat completion response, or []."""
    choices = resp.get("choices") or []
    if not choices:
        return []
    msg = choices[0].get("message") or {}
    return msg.get("tool_calls") or []


def build_tools_payload(schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Wrap each registry schema in OpenAI's function format."""
    return [
        {"type": "function", "function": schema} for schema in schemas
    ]


def run_conversation(client: TokenHubClient, user_text: str) -> str:
    """Single-turn tool-use loop (synchronous). Returns the final assistant text."""
    tools_payload = build_tools_payload(registry.list_schemas())
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_text}]

    for round_idx in range(MAX_TOOL_ROUNDS):
        resp = client.chat(messages, tools=tools_payload)
        msg = (resp.get("choices") or [{}])[0].get("message") or {}

        # If no tool calls, this is the final answer.
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return msg.get("content") or ""

        # Persist the assistant message (with tool_calls) into the conversation.
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        # Execute each tool call and append its result.
        import time
        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            raw_args = call.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            call_id = call.get("id", "")

            t0 = time.perf_counter()
            result = registry.dispatch(name, args)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            log_tool_call(name, args, result, elapsed_ms)

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result,
            })

    return "(max tool rounds reached without a final answer)"


async def run_conversation_async(client: AsyncTokenHubClient, user_text: str) -> str:
    """Single-turn tool-use loop (asynchronous). Returns the final assistant text.

    Non-blocking version for high-concurrency scenarios.
    """
    tools_payload = build_tools_payload(registry.list_schemas())
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_text}]

    for round_idx in range(MAX_TOOL_ROUNDS):
        resp = await client.chat(messages, tools=tools_payload)
        msg = (resp.get("choices") or [{}])[0].get("message") or {}

        # If no tool calls, this is the final answer.
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return msg.get("content") or ""

        # Persist the assistant message (with tool_calls) into the conversation.
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        # Execute each tool call and append its result.
        # Note: Tools are currently sync; wrap in executor for true async
        import time
        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            raw_args = call.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            call_id = call.get("id", "")

            t0 = time.perf_counter()
            result = registry.dispatch(name, args)  # sync; use executor if blocking
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            log_tool_call(name, args, result, elapsed_ms)

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result,
            })

    return "(max tool rounds reached without a final answer)"
