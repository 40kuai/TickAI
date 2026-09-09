"""Tool routes - list registered tools and execute them by name.

All operations reuse the global registry singleton from
hermes.tools.registry.

Security: only chat-visible (whitelisted) tools may be run from the Tools
page. Bare SSH tools that accept arbitrary host/password are rejected, and
every executed tool is persisted to the audit log.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from hermes.tools.registry import registry, is_chat_visible

from .deps import get_current_user

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolRunRequest(BaseModel):
    args: Dict[str, Any] = {}


@router.get("")
def list_tools(user=Depends(get_current_user)):
    """List tools exposed to the conversational LLM, with governance metadata.

    P3「读全开」: 白名单内只读工具自动全开 + run_selfheal(唯一写入口),
    与对话侧 payload 完全一致(registry.list_chat_tools)。
    """
    return registry.list_chat_tools()


@router.post("/{name}/run")
def run_tool(name: str, req: ToolRunRequest, user=Depends(get_current_user)):
    """Execute a chat-visible tool by name with the given arguments."""
    if not registry.has(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{name}' not found",
        )
    # Authorization: reject non-whitelisted tools (e.g. bare-SSH tools that
    # accept arbitrary host/password) — they are internal-only.
    if not is_chat_visible(name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tool '{name}' is not allowed to be run from the Tools page",
        )
    t0 = time.perf_counter()
    result_json = registry.dispatch(name, req.args)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    # Audit: persist every manual tool run (best-effort, never breaks response).
    try:
        from hermes.tools.ssh.runner import persist_tool_run

        sid = req.args.get("server_id")
        if isinstance(sid, bool) or not isinstance(sid, int) or sid <= 0:
            sid = None
        persist_tool_run(
            server_id=sid,
            command_label=name,
            result_json=result_json,
            triggered_by="user_button",
            duration_ms=elapsed_ms,
        )
    except Exception:  # noqa: BLE001
        pass
    return json.loads(result_json)
