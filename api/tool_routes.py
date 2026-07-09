"""Tool routes - list registered tools and execute them by name.

All operations reuse the global registry singleton from
hermes.tools.registry.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from hermes.tools.registry import registry

from .deps import get_current_user

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolRunRequest(BaseModel):
    args: Dict[str, Any] = {}


@router.get("")
def list_tools(user=Depends(get_current_user)):
    """List all registered tool schemas."""
    return registry.list_schemas()


@router.post("/{name}/run")
def run_tool(name: str, req: ToolRunRequest, user=Depends(get_current_user)):
    """Execute a registered tool by name with the given arguments."""
    if not registry.has(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{name}' not found",
        )
    result_json = registry.dispatch(name, req.args)
    return json.loads(result_json)
