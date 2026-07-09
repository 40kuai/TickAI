"""History routes - query run history with filtering and detail view."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from hermes.data.db import session_scope
from hermes.data.models import RunRecord, Server
from hermes.tools.registry import registry

from .deps import get_current_user

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
def query_history(
    user=Depends(get_current_user),
    server_name: Optional[str] = Query(None, description="Filter by server name (exact match)"),
    status: Optional[str] = Query(None, description="Filter by run status"),
    triggered_by: Optional[str] = Query(None, description="Filter by trigger source"),
    since: Optional[str] = Query(None, description="Time window like '1h', '1d', '7d'"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
):
    """Query run history with optional filters."""
    args: dict = {"limit": limit}
    if server_name:
        args["server_name"] = server_name
    if status:
        args["status"] = status
    if triggered_by:
        args["triggered_by"] = triggered_by
    if since:
        args["since"] = since

    result_json = registry.dispatch("query_runs", args)
    return json.loads(result_json)


@router.get("/{run_id}")
def get_run_detail(run_id: int, user=Depends(get_current_user)):
    """Get full detail of a single run record."""
    with session_scope() as s:
        r = s.get(RunRecord, run_id)
        if r is None:
            raise HTTPException(404, "记录不存在")
        sv = s.get(Server, r.server_id) if r.server_id else None
        # Parse structured result for display
        result_data = None
        if r.structured_result:
            try:
                result_data = json.loads(r.structured_result)
            except Exception:
                result_data = None
        # Parse triggered context
        ctx = None
        if r.triggered_context:
            try:
                ctx = json.loads(r.triggered_context)
            except Exception:
                ctx = None
        return {
            "id": r.id,
            "server_id": r.server_id,
            "server_name": sv.name if sv else None,
            "server_host": sv.host if sv else None,
            "command": r.command,
            "status": r.status,
            "exit_code": r.exit_code,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": r.duration_ms,
            "stdout": r.stdout,
            "stderr": r.stderr,
            "result": result_data,
            "triggered_by": r.triggered_by,
            "triggered_context": ctx,
        }
