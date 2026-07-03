"""
caller.py — 腾讯 tokenhub 专业版 LLM 工具调用 demo

用法：
    # 一次性查询
    python caller.py "帮我掷一个 d20"

    # 进入 REPL
    python caller.py

环境变量（在 .env 或 shell 中）：
    TOKENHUB_API_KEY     必填
    TOKENHUB_MODEL       默认 deepseek-v4-flash
    TOKENHUB_BASE_URL    默认 https://tokenhub.tencentmaas.com/plan/v3
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# Importing the tool module has the side effect of registering it
# (dice tool removed — was demo)
import hermes.tools.ssh.disk  # noqa: F401
from hermes.tools.registry import registry, tool_error


# ============================================================
# Configuration
# ============================================================
DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com/plan/v3"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_TOOL_ROUNDS = 5
HTTP_TIMEOUT_SECONDS = 60.0


# ============================================================
# .env loader (stdlib only)
# ============================================================
def parse_env_file(text: str) -> Dict[str, str]:
    """Parse a simple KEY=VALUE .env file. No external deps."""
    out: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # strip inline comment
        if value and not (value.startswith('"') or value.startswith("'")):
            value = value.split(" #", 1)[0].rstrip()
        # strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_env_file(path: Path = Path(".env")) -> Dict[str, str]:
    """Load a .env file if it exists. Returns empty dict otherwise."""
    if not path.exists():
        return {}
    return parse_env_file(path.read_text(encoding="utf-8"))


# ============================================================
# Tools payload — registry schemas → OpenAI function format
# ============================================================
def build_tools_payload(schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Wrap each registry schema in OpenAI's {type:function, function:{...}} envelope."""
    return [
        {"type": "function", "function": schema} for schema in schemas
    ]


# ============================================================
# LLM call logging — writes to stderr so stdout stays clean
# ============================================================
_LOG_BAR = "─" * 60
_LOG_CONTENT_PREVIEW = 200
_LOG_PAYLOAD_PREVIEW = 4000


def _now_hms() -> str:
    return time.strftime("%H:%M:%S")


def _format_ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f}ms"


def _summarize_tool_calls(tool_calls):
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


def _log_llm_request(payload, endpoint):
    messages = payload.get("messages", [])
    tools = payload.get("tools") or []
    payload_str = json.dumps(payload, ensure_ascii=False, indent=2)
    lines = [
        "",
        _LOG_BAR,
        f"[LLM CALL] {_now_hms()}",
        f"endpoint: {endpoint}",
        f"model: {payload.get('model', '?')}",
        f"messages: {len(messages)}",
    ]
    if tools:
        lines.append(f"tools: {len(tools)}")
    lines += [f"payload: {payload_str}", _LOG_BAR, ""]
    print("\n".join(lines), file=sys.stderr, flush=True)


def _log_llm_response(resp, elapsed_ms, status_code):
    choices = resp.get("choices") or []
    first = choices[0] if choices else {}
    msg = first.get("message") or {}
    finish_reason = first.get("finish_reason", "?")
    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content")
    usage = resp.get("usage") or {}
    # elapsed_ms can be int/float (ms) or pre-formatted string
    if isinstance(elapsed_ms, (int, float)):
        elapsed_str = f"{elapsed_ms:.0f}ms"
    else:
        elapsed_str = str(elapsed_ms)
    lines = [
        "",
        _LOG_BAR,
        f"[LLM RESP] {_now_hms()} (latency={elapsed_str}, status={status_code})",
        f"finish_reason: {finish_reason}",
    ]
    if usage:
        lines.append(
            f"tokens: prompt={usage.get('prompt_tokens', '?')} "
            f"completion={usage.get('completion_tokens', '?')} "
            f"total={usage.get('total_tokens', '?')}"
        )
    if content:
        lines.append(f"content: {content}")
    if tool_calls:
        lines.append(f"tool_calls: {len(tool_calls)}")
        lines.extend(_summarize_tool_calls(tool_calls))
    lines += [_LOG_BAR, ""]
    print("\n".join(lines), file=sys.stderr, flush=True)


def _log_llm_error(exc, elapsed_ms):
    if isinstance(elapsed_ms, (int, float)):
        elapsed_str = f"{elapsed_ms:.0f}ms"
    else:
        elapsed_str = str(elapsed_ms)
    print(
        f"\n{_LOG_BAR}\n[LLM ERROR] {_now_hms()} (latency={elapsed_str})\n"
        f"{type(exc).__name__}: {exc}\n{_LOG_BAR}\n",
        file=sys.stderr,
        flush=True,
    )


# ============================================================
class TokenHubClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: float = HTTP_TIMEOUT_SECONDS,
                 verbose: bool = True):
        if not api_key:
            raise ValueError("TOKENHUB_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.verbose = verbose

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def chat(self, messages, tools=None, *, client_factory=None):
        """POST a chat completion request. Returns the raw JSON dict."""
        payload = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = self._endpoint()
        if self.verbose:
            _log_llm_request(payload, endpoint)

        t0 = time.perf_counter()
        client_ctor = client_factory or (lambda **kw: httpx.Client(**kw))
        try:
            with client_ctor(timeout=self.timeout) as client:
                resp = client.post(endpoint, headers=headers, json=payload)
                elapsed = _format_ms(time.perf_counter() - t0)
                if resp.status_code >= 400:
                    body = resp.text[:500]
                    err = RuntimeError(f"TokenHub HTTP {resp.status_code}: {body}")
                    if self.verbose:
                        _log_llm_error(err, elapsed)
                    raise err
                data = resp.json()
        except Exception as exc:
            # Avoid double-logging the same HTTP 4xx we already logged above
            already_logged = isinstance(exc, RuntimeError) and "TokenHub HTTP" in str(exc)
            if not already_logged:
                elapsed = _format_ms(time.perf_counter() - t0)
                if self.verbose:
                    _log_llm_error(exc, elapsed)
            raise
        if self.verbose:
            _log_llm_response(data, elapsed, resp.status_code)
        return data


# ============================================================
# Tool-call loop
# ============================================================
def _tool_calls_from_response(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract tool_calls from a chat completion response, or []."""
    choices = resp.get("choices") or []
    if not choices:
        return []
    msg = choices[0].get("message") or {}
    return msg.get("tool_calls") or []


def run_conversation(client: TokenHubClient, user_text: str) -> str:
    """Single-turn tool-use loop. Returns the final assistant text."""
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
        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            raw_args = call.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            call_id = call.get("id", "")

            print(f"  [tool] {name}({args})", file=sys.stderr)
            result = registry.dispatch(name, args)
            truncated = result
            entry = registry.get(name)
            if entry and len(result) > entry["max_result_size_chars"]:
                truncated = result[: entry["max_result_size_chars"]] + "...(truncated)"
            print(f"  [→] {truncated}", file=sys.stderr)

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result,
            })

    return "(max tool rounds reached without a final answer)"


# ============================================================
# CLI
# ============================================================
def _resolve_config() -> Dict[str, str]:
    """Merge .env with process env; .env takes lower priority than real env."""
    file_env = load_env_file(Path(".env"))
    merged: Dict[str, str] = {}
    merged.update(file_env)
    merged.update({k: v for k, v in os.environ.items() if k.startswith("TOKENHUB_")})
    return merged


def _print_available_tools() -> None:
    print("Registered tools:", file=sys.stderr)
    for schema in registry.list_schemas():
        print(f"  - {schema['name']}", file=sys.stderr)


def main(argv: List[str]) -> int:
    config = _resolve_config()
    api_key = config.get("TOKENHUB_API_KEY", "")
    base_url = config.get("TOKENHUB_BASE_URL", DEFAULT_BASE_URL)
    model = config.get("TOKENHUB_MODEL", DEFAULT_MODEL)

    if not api_key:
        print("ERROR: TOKENHUB_API_KEY not set.", file=sys.stderr)
        print("Set it in a .env file or as an environment variable.", file=sys.stderr)
        print("See .env.example for the format.", file=sys.stderr)
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
