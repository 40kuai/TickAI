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
        read_only: bool = True,
        risk: str = "low",
    ) -> None:
        """Register a tool. `schema["name"]` must match `name`.

        read_only/risk: P0 能力治理元数据 — 管理页按此分级展示, 对话侧
        「读全开」按 read_only 过滤; 默认只读低危(default-deny)。
        """
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
            "read_only": read_only,
            "risk": risk,
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

    def list_meta(self) -> List[Dict[str, Any]]:
        """Return per-tool governance metadata (schema + read_only/risk/emoji).

        Used by the management page (工具清单) and the chat-side
        read_only filtering; never exposed to the LLM as tool schemas.
        """
        return [
            {
                "name": t["name"],
                "schema": t["schema"],
                "toolset": t["toolset"],
                "emoji": t["emoji"],
                "read_only": t["read_only"],
                "risk": t["risk"],
            }
            for t in self._tools.values()
        ]

    def list_chat_tools(self) -> List[Dict[str, Any]]:
        """对话「读全开」工具集: 白名单(凭据隔离边界)内只读工具 + 唯一受控写入口.

        P3 语义: 读全开 = 新增只读工具只要进白名单即自动对对话可见(无需手工
        维护 payload); 写工具永不按只读暴露(read_only=False 即使误入白名单
        也被过滤), run_selfheal 是唯一例外(走统一审批出口, 高危挂单/低危直执)。
        """
        out: List[Dict[str, Any]] = []
        for t in self.list_meta():
            if t["name"] == "run_selfheal":
                out.append(t)
            elif is_chat_visible(t["name"]) and t["read_only"]:
                out.append(t)
        return out

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


# ============================================================
# AI operation scope — chat-visible tools whitelist
# ============================================================
# The conversational LLM (Web / Feishu) and the Tools page's manual run
# endpoint must NOT be able to invoke every registered tool. Specifically,
# the "bare" SSH tools (check_resources / list_services) accept arbitrary
# host + username + password and would let the LLM connect to any host with
# caller-supplied credentials — bypassing the server_id-based credential
# isolation. The LLM should only reach servers through the *_on_server
# wrappers (which resolve credentials from the DB) plus read-only
# observability / query tools.
CHAT_VISIBLE_TOOLS: tuple[str, ...] = (
    # DB / server inventory
    "list_servers",
    "query_runs",
    # SSH via server_id (credentials resolved from DB, never host/password)
    "check_disk_usage",
    "check_resources_on_server",
    "list_services_on_server",
    # K8s read-only
    "check_k8s_nodes",
    "check_k8s_pods",
    "check_k8s_events",
    "check_k8s_deployments",
    "check_k8s_services",
    "list_k8s_contexts",
    # LDAP user lookup
    "ldap_search_user",
    # Observability (read-only)
    "prometheus_service_discovery",
    "prometheus_service_health",
    "prometheus_metric_query",
    "nightingale_history_alerts",
    "nightingale_active_alerts",
    "jenkins_service_jobs",
    "jenkins_build_records",
    # Skill execution
    "run_skill",
    # 受控自愈闭环(探测→分级→低危自主/高危审批→验证,写命令限模板白名单)
    "run_selfheal",
    # 只读日志/磁盘/容器清单扫描(通道二 AI 分析数据源)
    "scan_log_cleanup",
)


def is_chat_visible(name: str) -> bool:
    """Whether a tool may be exposed to the conversational LLM / Tools page.

    Default-deny: any tool not explicitly whitelisted is NOT visible.
    """
    return name in CHAT_VISIBLE_TOOLS


# Process-wide singleton — tools import this directly
registry = ToolRegistry()
