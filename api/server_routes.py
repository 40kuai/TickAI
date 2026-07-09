"""Server management routes - CRUD and SSH inspection operations.

Listing reuses the list_servers tool via registry.dispatch(). Check
operations (disk / resources / services) dispatch the corresponding SSH
tools with credentials looked up from the DB, then persist a RunRecord
via hermes.tools.ssh.runner.persist_tool_run() for audit.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from hermes.data.db import session_scope
from hermes.data.models import Server
from hermes.tools.registry import registry
from hermes.tools.ssh import runner as ssh_runner

from .deps import get_current_user

router = APIRouter(prefix="/api/servers", tags=["servers"])


class ServerCreate(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    password: str
    tags: str = ""
    notes: str = ""


@router.get("")
def list_servers(user=Depends(get_current_user)):
    """List all servers (passwords masked). Reuses list_servers tool."""
    result_json = registry.dispatch("list_servers", {"active_only": False})
    return json.loads(result_json)


@router.post("")
def add_server(server: ServerCreate, user=Depends(get_current_user)):
    """Add a new server."""
    with session_scope() as s:
        sv = Server(
            name=server.name,
            host=server.host,
            port=server.port,
            username=server.username,
            password=server.password,
            tags=server.tags,
            notes=server.notes,
        )
        s.add(sv)
        s.flush()
        result = sv.to_dict()
    return result


@router.delete("/{server_id}")
def delete_server(server_id: int, user=Depends(get_current_user)):
    """Soft-delete a server (mark inactive)."""
    with session_scope() as s:
        sv = s.get(Server, server_id)
        if sv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found",
            )
        sv.is_active = False
    return {"detail": "Server deleted"}


def _get_server_credentials(server_id: int) -> dict:
    """Look up a server's SSH credentials. Raises 404 if not found."""
    with session_scope() as s:
        sv = s.get(Server, server_id)
        if sv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found",
            )
        return {
            "host": sv.host,
            "port": sv.port,
            "username": sv.username,
            "password": sv.password,
        }


def _dispatch_and_persist(
    server_id: int, tool_name: str, command_label: str
) -> dict:
    """Dispatch an SSH tool with server credentials and persist the audit."""
    args = _get_server_credentials(server_id)
    result_json = registry.dispatch(tool_name, args)
    # Persist audit record (best-effort, never breaks the response)
    try:
        ssh_runner.persist_tool_run(
            server_id, command_label, result_json, "user_button"
        )
    except Exception:
        pass
    return json.loads(result_json)


@router.post("/{server_id}/check_disk")
def check_disk(server_id: int, user=Depends(get_current_user)):
    """Check disk usage on a server via SSH."""
    return _dispatch_and_persist(server_id, "check_disk_usage", "df -Th")


@router.post("/{server_id}/check_resources")
def check_resources(server_id: int, user=Depends(get_current_user)):
    """Check CPU/memory resources on a server via SSH."""
    return _dispatch_and_persist(server_id, "check_resources", "check_resources")


@router.post("/{server_id}/list_services")
def list_services(server_id: int, user=Depends(get_current_user)):
    """List systemd services on a server via SSH."""
    return _dispatch_and_persist(server_id, "list_services", "list_services")
