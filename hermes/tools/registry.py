"""Tool registry — the contract every tool plugs into.

Usage (in a tool file):

    from hermes.tools.registry import registry, tool_error, tool_result

    def my_handler(args, **kwargs):
        return tool_result(value=42)

    registry.register(
        name="my_tool",
        schema={...},                 # OpenAI-style function schema
        handler=my_handler,
        check_fn=lambda: True,        # availability check
    )
"""
from __future__ import annotations

import json
import traceback
from typing import Any, Callable, Dict, List, Optional


# ============================================================
# Result helpers — every handler must return a JSON string
# ============================================================
def tool_result(**kwargs: Any) -> str:
    """Wrap a successful handler payload as a JSON string."""
    return json.dumps(kwargs, ensure_ascii=False)


def tool_error(message: str) -> str:
    """Wrap a handler error as a JSON string with a single 'error' key."""
    return json.dumps({"error": message}, ensure_ascii=False)


# ============================================================
# Registry — holds all registered tools
# ============================================================
Handler = Callable[..., str]
CheckFn = Callable[[], bool]


class ToolRegistry:
    """In-process registry of LLM-callable tools.

    Stores the schema (sent to the LLM) and the handler (executed locally).
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        *,
        name: str,
        schema: Dict[str, Any],
        handler: Handler,
        check_fn: CheckFn,
        toolset: str = "default",
        emoji: str = "",
        max_result_size_chars: int = 8000,
    ) -> None:
        """Register a tool. `schema["name"]` must match `name`."""
        if schema.get("name") and schema["name"] != name:
            raise ValueError(
                f"schema.name={schema['name']!r} does not match name={name!r}"
            )
        # Idempotent: overwriting an existing entry is allowed. This matters
        # when the host process reloads modules (e.g. Streamlit auto-reload)
        # and re-runs the registration side-effect.
        self._tools[name] = {
            "name": name,
            "schema": schema,
            "handler": handler,
            "check_fn": check_fn,
            "toolset": toolset,
            "emoji": emoji,
            "max_result_size_chars": max_result_size_chars,
        }

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return self._tools.get(name)

    def list_schemas(self) -> List[Dict[str, Any]]:
        """Return the list of LLM-facing schemas in registration order."""
        return [t["schema"] for t in self._tools.values()]

    def list_schemas_by_toolset(self, toolset: str) -> List[Dict[str, Any]]:
        """Return schemas filtered by toolset attribute."""
        return [
            t["schema"] for t in self._tools.values()
            if t.get("toolset") == toolset
        ]

    def dispatch(self, name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        """Execute a registered handler by name. Never raises — returns JSON."""
        entry = self._tools.get(name)
        if entry is None:
            return tool_error(f"unknown tool: {name}")
        try:
            if not entry["check_fn"]():
                return tool_error(f"tool not available: {name}")
            return entry["handler"](args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — we want to capture *any* handler error
            return tool_error(f"handler raised {type(exc).__name__}: {exc}")


# Process-wide singleton — tools import this directly
registry = ToolRegistry()
