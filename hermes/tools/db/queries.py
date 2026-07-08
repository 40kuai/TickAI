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
        "List all registered servers. Optionally filter by tag, name, or active status. "
        "Returns a JSON object with `servers` (array) and `count` (int). "
        "Passwords are masked — never visible to the LLM."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "Filter by tag (e.g., 'web', 'db'). Exact match.",
            },
            "name": {
                "type": "string",
                "description": "Filter by server name (substring match).",
            },
            "active_only": {
                "type": "boolean",
                "description": "If true, exclude inactive servers. Default true.",
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
                "port": sv.port,
                "user": sv.username,
                "password": "***",  # security: never expose
                "tags": sv.tags or "",
                "is_active": sv.is_active,
                "last_seen": sv.last_seen_at.isoformat() if sv.last_seen_at else None,
            })
    return tool_result(count=len(out), servers=out)


QUERY_RUNS_SCHEMA = {
    "name": "query_runs",
    "description": (
        "Query historical run records. Filter by server name, status, triggered_by, "
        "or a `since` time window. Returns a JSON object with `runs` (array) and `count` (int). "
        "Each run has id, server_id, server_name, command, status, started_at, duration_ms, triggered_by."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_name": {"type": "string", "description": "Filter by server name (exact match)."},
            "status": {
                "type": "string",
                "enum": ["success", "failed", "pending"],
                "description": "Filter by run status.",
            },
            "triggered_by": {
                "type": "string",
                "description": "Filter by trigger source (e.g., 'user_button', 'llm_tool_call', 'scheduled').",
            },
            "since": {
                "type": "string",
                "description": "Time window like '1h', '1d', '7d' (relative to now).",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of records to return. Default 100.",
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
            sv = s.get(Server, r.server_id)
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
