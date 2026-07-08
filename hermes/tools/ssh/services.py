"""LLM-facing tools for system inspection (CPU / memory / services).

Each handler:
  1. Looks up the server's credentials from the DB (server_id is what the LLM sees)
  2. Calls the underlying SSH handler from hermes.tools.system
  3. Persists the run to the audit log (triggered_by="llm_tool_call")
  4. Returns the structured result to the LLM — never includes the password

The LLM never sees host/username/password — only server_id. The tools also
never start/stop services; the user must do that via the UI.
"""
from __future__ import annotations

import json
from typing import Any

from hermes.tools.registry import registry, tool_error, tool_result
from hermes.tools.ssh.resources import check_resources_handler, list_services_handler
from hermes.data.db import session_scope
from hermes.data.models import Server
from . import runner as ssh_runner


RESOURCES_ON_SERVER_SCHEMA = {
    "name": "check_resources_on_server",
    "description": (
        "Read-only CPU/memory/process inspection of a server in this OpsTicket "
        "instance. Pass the server_id (an integer, get it from list_servers). "
        "Never modifies the server. Returns load averages, memory/swap usage, "
        "top CPU processes, and a pressure_level classification.\n\n"
        "Use this to diagnose 'why is this server slow?' — look at top_processes "
        "to identify the resource consumer, then recommend optimization. "
        "Do NOT restart services from this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_id": {
                "type": "integer",
                "description": "Server ID from list_servers. Must be an integer.",
            }
        },
        "required": ["server_id"],
    },
}

SERVICES_ON_SERVER_SCHEMA = {
    "name": "list_services_on_server",
    "description": (
        "Read-only enumeration of systemd-managed services on a server in this "
        "OpsTicket instance. Pass the server_id (an integer). Returns each "
        "service's name, state, sub_state, and is_abnormal flag.\n\n"
        "Use this to find failed or inactive services. The is_abnormal flag "
        "marks anything that's not actively running normally. NEVER starts or "
        "stops services — recommend the user do that via the UI."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_id": {
                "type": "integer",
                "description": "Server ID from list_servers. Must be an integer.",
            }
        },
        "required": ["server_id"],
    },
}


def _get_server_or_error(server_id: Any):
    """Look up server, return (server, error_string). Exactly one is non-None."""
    if not isinstance(server_id, int):
        return None, "server_id must be an integer"
    with session_scope() as s:
        server = s.query(Server).filter(Server.id == server_id).first()
        if server is None:
            return None, f"server id={server_id} not found (use list_servers to find valid IDs)"
        return server, None


def check_resources_on_server_handler(args: dict, **kwargs) -> str:
    server, err = _get_server_or_error(args.get("server_id"))
    if err:
        return tool_error(err)

    result_str = check_resources_handler({
        "host": server.host,
        "port": server.port,
        "username": server.username,
        "password": server.password,
    })

    # Persist audit (succeeds even if SSH failed — we record the failure)
    try:
        ssh_runner.persist_tool_run(
            server_id=server.id,
            command_label="check_resources",
            result_json=result_str,
            triggered_by="llm_tool_call",
            triggered_context={
                "tool_name": "check_resources_on_server",
                "server_name": server.name,
            },
        )
    except Exception:  # noqa: BLE001
        # Audit failure shouldn't break the LLM response
        pass

    result = json.loads(result_str)
    if "error" in result:
        # Return a clean error envelope (no raw SSH details)
        return tool_error(result["error"])

    return tool_result(server_name=server.name, **result)


def list_services_on_server_handler(args: dict, **kwargs) -> str:
    server, err = _get_server_or_error(args.get("server_id"))
    if err:
        return tool_error(err)

    result_str = list_services_handler({
        "host": server.host,
        "port": server.port,
        "username": server.username,
        "password": server.password,
    })

    try:
        ssh_runner.persist_tool_run(
            server_id=server.id,
            command_label="list_services",
            result_json=result_str,
            triggered_by="llm_tool_call",
            triggered_context={
                "tool_name": "list_services_on_server",
                "server_name": server.name,
            },
        )
    except Exception:  # noqa: BLE001
        pass

    result = json.loads(result_str)
    if "error" in result:
        return tool_error(result["error"])

    return tool_result(server_name=server.name, **result)


registry.register(
    name="check_resources_on_server",
    toolset="system",
    schema=RESOURCES_ON_SERVER_SCHEMA,
    handler=check_resources_on_server_handler,
    check_fn=lambda: True,
    emoji="📊",
    max_result_size_chars=8000,
)

registry.register(
    name="list_services_on_server",
    toolset="system",
    schema=SERVICES_ON_SERVER_SCHEMA,
    handler=list_services_on_server_handler,
    check_fn=lambda: True,
    emoji="⚙️",
    max_result_size_chars=16000,
)
