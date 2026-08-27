"""DB query tools (list_servers, query_runs) — read-only.

These are LLM-callable tools that query the local SQLite database. They never
expose secrets (passwords are masked to "***").
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from hermes.data.db import session_scope
from hermes.data.models import RunRecord, Server
from hermes.tools.registry import registry, tool_error, tool_result


LIST_SERVERS_SCHEMA = {
    "name": "list_servers",
    "description": (
        "列出所有已注册的服务器。可按 tag、名称或启用状态过滤。"
        "返回包含 `servers`(数组)和 `count`(数量)的 JSON。"
        "SSH 凭据以 ssh_credential_id 引用——绝不会向 LLM 暴露。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "按标签过滤(如 'web'、'db')。精确匹配。",
            },
            "name": {
                "type": "string",
                "description": "按服务器名称过滤(子串匹配)。",
            },
            "active_only": {
                "type": "boolean",
                "description": "为 true 时排除停用服务器。默认 true。",
            },
        },
        "required": [],
    },
}


def list_servers_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    """Return all servers (or filtered). Passwords masked."""
    tag = args.get("tag")
    name_substr = args.get("name")
    active_only = args.get("active_only", True)

    with session_scope() as s:
        q = s.query(Server)
        if active_only:
            q = q.filter_by(is_active=True)
        servers = q.all()
        out = []
        for sv in servers:
            # Tag filter
            if tag and tag not in (sv.tags or "").split(","):
                continue
            # Name filter
            if name_substr and name_substr not in (sv.name or ""):
                continue
            out.append({
                "id": sv.id,
                "name": sv.name,
                "host": sv.host,
                "tags": [t.strip() for t in (sv.tags or "").split(",") if t.strip()],
                "is_active": sv.is_active,
                "ssh_credential_id": sv.ssh_credential_id,
                "ssh_credential_name": sv.ssh_credential.name if sv.ssh_credential else None,
                "last_seen": sv.last_seen_at.isoformat() if sv.last_seen_at else None,
            })
    return tool_result(count=len(out), servers=out)


QUERY_RUNS_SCHEMA = {
    "name": "query_runs",
    "description": (
        "查询历史运行记录。可按服务器名称、状态、触发来源或 `since` 时间窗口过滤。"
        "返回包含 `runs`(数组)和 `count`(数量)的 JSON。"
        "每条 run 包含 id、server_id、server_name、command、status、started_at、duration_ms、triggered_by。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_name": {"type": "string", "description": "按服务器名称过滤(精确匹配)。"},
            "status": {
                "type": "string",
                "enum": ["success", "failed", "pending"],
                "description": "按运行状态过滤。",
            },
            "triggered_by": {
                "type": "string",
                "description": "按触发来源过滤(如 'user_button'、'llm_tool_call'、'scheduled')。",
            },
            "since": {
                "type": "string",
                "description": "时间窗口,如 '1h'、'1d'、'7d'(相对当前时间)。",
            },
            "limit": {
                "type": "integer",
                "description": "最大返回记录数。默认 100。",
            },
        },
        "required": [],
    },
}


def _parse_since(since: str) -> datetime | None:
    """Parse a '1d' / '2h' style string into a UTC datetime."""
    if not since:
        return None
    unit = since[-1]
    try:
        n = int(since[:-1])
    except ValueError:
        return None
    if unit == "h":
        return datetime.utcnow() - timedelta(hours=n)
    if unit == "d":
        return datetime.utcnow() - timedelta(days=n)
    if unit == "m":
        return datetime.utcnow() - timedelta(minutes=n)
    return None


def query_runs_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    """Return run records, filtered."""
    server_name = args.get("server_name")
    status = args.get("status")
    triggered_by = args.get("triggered_by")
    since = args.get("since")
    limit = args.get("limit", 100)

    cutoff = _parse_since(since) if since else None

    with session_scope() as s:
        q = s.query(RunRecord)
        if status:
            q = q.filter_by(status=status)
        if triggered_by:
            q = q.filter_by(triggered_by=triggered_by)
        if cutoff:
            q = q.filter(RunRecord.started_at >= cutoff)
        if server_name:
            sv = s.query(Server).filter_by(name=server_name).first()
            if sv is None:
                return tool_result(count=0, runs=[])
            q = q.filter(RunRecord.server_id == sv.id)
        runs = q.order_by(RunRecord.started_at.desc()).limit(limit).all()
        out = []
        for r in runs:
            sv = s.get(Server, r.server_id) if r.server_id else None
            out.append({
                "id": r.id,
                "server_id": r.server_id,
                "server_name": sv.name if sv else None,
                "command": r.command,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration_ms": r.duration_ms,
                "triggered_by": r.triggered_by,
            })
    return tool_result(count=len(out), runs=out)


# Register tools (uses the same style as other tools)
def _db_tools_available() -> bool:
    """DB 工具总是可用"""
    return True


registry.register(
    name="list_servers",
    schema=LIST_SERVERS_SCHEMA,
    handler=list_servers_handler,
    check_fn=_db_tools_available,
    toolset="ops",
    emoji="📋",
)


registry.register(
    name="query_runs",
    schema=QUERY_RUNS_SCHEMA,
    handler=query_runs_handler,
    check_fn=_db_tools_available,
    toolset="ops",
    emoji="📜",
)
