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

from hermes.tools.registry import registry, tool_error, tool_result
from hermes.tools.ssh.resources import check_resources_handler, list_services_handler
from hermes.data.db import session_scope
from hermes.data.models import Server, SSHCredential
from . import runner as ssh_runner


RESOURCES_ON_SERVER_SCHEMA = {
    "name": "check_resources_on_server",
    "description": (
        "只读检查本 OpsTicket 实例中某台服务器的 CPU/内存/进程状况。"
        "传入 server_id(整数,可从 list_servers 获取)。绝不会修改服务器。"
        "返回负载均值、内存/Swap 使用、CPU 占用最高的进程及压力等级 pressure_level 分类。\n\n"
        "用于诊断'为什么这台服务器很慢'——查看 top_processes 找出资源占用者,再给出优化建议。"
        "请勿通过本工具重启服务。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_id": {
                "type": "integer",
                "description": "来自 list_servers 的服务器 ID。必须是整数。",
            }
        },
        "required": ["server_id"],
    },
}

SERVICES_ON_SERVER_SCHEMA = {
    "name": "list_services_on_server",
    "description": (
        "只读枚举本 OpsTicket 实例中某台服务器上由 systemd 管理的服务。"
        "传入 server_id(整数)。返回每个服务的名称、state、sub_state 及 is_abnormal 标记。\n\n"
        "用于查找失败或未运行的服务。is_abnormal 标记任何非正常运行状态的服务。"
        "绝不会启动/停止服务——请建议用户通过 UI 操作。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "server_id": {
                "type": "integer",
                "description": "来自 list_servers 的服务器 ID。必须是整数。",
            }
        },
        "required": ["server_id"],
    },
}


def check_resources_on_server_handler(args: dict, **kwargs) -> str:
    server_id = args.get("server_id")
    if not isinstance(server_id, int):
        return tool_error("server_id must be an integer")

    with session_scope() as s:
        server = s.query(Server).filter(Server.id == server_id).first()
        if server is None:
            return tool_error(f"server id={server_id} not found (use list_servers to find valid IDs)")
        # Use bound credential, or fall back to default
        cred = server.ssh_credential
        if cred is None:
            cred = s.query(SSHCredential).filter(SSHCredential.is_default == True).first()
        if cred is None:
            return tool_error(f"server '{server.name}' has no SSH credential bound and no default set")
        host = server.host
        server_name = server.name
        server_pk = server.id
        cred_args = cred.to_connect_args()

    result_str = check_resources_handler({"host": host, **cred_args})

    # Persist audit (succeeds even if SSH failed — we record the failure)
    try:
        ssh_runner.persist_tool_run(
            server_id=server_pk,
            command_label="check_resources",
            result_json=result_str,
            triggered_by="llm_tool_call",
            triggered_context={
                "tool_name": "check_resources_on_server",
                "server_name": server_name,
            },
        )
    except Exception:  # noqa: BLE001
        # Audit failure shouldn't break the LLM response
        pass

    result = json.loads(result_str)
    if "error" in result:
        # Return a clean error envelope (no raw SSH details)
        return tool_error(result["error"])

    return tool_result(server_name=server_name, **result)


def list_services_on_server_handler(args: dict, **kwargs) -> str:
    server_id = args.get("server_id")
    if not isinstance(server_id, int):
        return tool_error("server_id must be an integer")

    with session_scope() as s:
        server = s.query(Server).filter(Server.id == server_id).first()
        if server is None:
            return tool_error(f"server id={server_id} not found (use list_servers to find valid IDs)")
        # Use bound credential, or fall back to default
        cred = server.ssh_credential
        if cred is None:
            cred = s.query(SSHCredential).filter(SSHCredential.is_default == True).first()
        if cred is None:
            return tool_error(f"server '{server.name}' has no SSH credential bound and no default set")
        host = server.host
        server_name = server.name
        server_pk = server.id
        cred_args = cred.to_connect_args()

    result_str = list_services_handler({"host": host, **cred_args})

    try:
        ssh_runner.persist_tool_run(
            server_id=server_pk,
            command_label="list_services",
            result_json=result_str,
            triggered_by="llm_tool_call",
            triggered_context={
                "tool_name": "list_services_on_server",
                "server_name": server_name,
            },
        )
    except Exception:  # noqa: BLE001
        pass

    result = json.loads(result_str)
    if "error" in result:
        return tool_error(result["error"])

    return tool_result(server_name=server_name, **result)


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
