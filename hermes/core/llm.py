"""
LLM integration facade.

This module re-exports from the split modules for backward compatibility.
New code should import directly from hermes.core.llm_client, etc.
"""
from __future__ import annotations

import sys
from typing import List

# Re-exports from split modules
from .llm_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    HTTP_TIMEOUT_SECONDS,
    TokenHubClient,
    AsyncTokenHubClient,
)
from .llm_logging import (
    log_request,
    log_response,
    log_error,
    log_tool_call,
)
from .tool_runner import (
    MAX_TOOL_ROUNDS,
    build_tools_payload,
    run_conversation,
    run_conversation_async,
)

# For backward compatibility with chat.py
# TokenHubClient is the main export


# ============================================================
# CLI (keep for debugging)
# ============================================================
def _print_available_tools() -> None:
    from ..tools.registry import registry
    print("Registered tools:", file=sys.stderr)
    for schema in registry.list_schemas():
        print(f"  - {schema['name']}", file=sys.stderr)


def main(argv: List[str]) -> int:
    """CLI entry point for testing LLM + tool calls."""
    # Use settings module (no duplicate .env parsing)
    from ..config.settings import get

    api_key = get("TOKENHUB_API_KEY", "")
    base_url = get("TOKENHUB_BASE_URL", DEFAULT_BASE_URL)
    model = get("TOKENHUB_MODEL", DEFAULT_MODEL)

    if not api_key:
        print("ERROR: TOKENHUB_API_KEY not set in .env", file=sys.stderr)
        return 2

    client = TokenHubClient(api_key=api_key, base_url=base_url, model=model)
    print(f"Using model={model} base_url={base_url}", file=sys.stderr)
    _print_available_tools()

    if len(argv) > 1:
        # One-shot mode
        user_text = " ".join(argv[1:])
        try:
            print(run_conversation(client, user_text))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # REPL mode
    print('Interactive mode. Type "exit" or Ctrl-D to quit.', file=sys.stderr)
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return 0
        if not line:
            continue
        if line in {"exit", "quit"}:
            return 0
        try:
            print(run_conversation(client, line))
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
