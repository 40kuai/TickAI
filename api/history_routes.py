"""History routes - query run history with filtering.

Reuses the query_runs tool via registry.dispatch() which delegates to
hermes.tools.db.queries.query_runs_handler.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

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
    """Query run history with optional filters. Reuses query_runs tool."""
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
