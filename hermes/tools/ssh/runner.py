"""SSH runner — executes commands via the SSH tool handlers and persists audit records.

Two execution paths share the same audit persistence:
  - run_command()       — for ad-hoc commands (used by Check Disk page)
  - persist_tool_run()  — for any tool handler that returns a JSON string
                          (used by LLM wrappers like check_disk_on_server,
                          check_resources_on_server, list_services_on_server)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from hermes.tools.ssh.disk import check_disk_handler

from hermes.data.db import session_scope
from hermes.data.models import RunRecord, Server


def run_command(
    server_id: int,
    command: str,
    triggered_by: str,
    triggered_context: Optional[dict] = None,
) -> RunRecord:
    """Execute `command` on the given server via check_disk_handler (df family only),
    persist the result, return the RunRecord.

    `triggered_by` is "user_button" or "llm_tool_call".
    """
    started_at = datetime.utcnow()
    with session_scope() as session:
        server = session.get(Server, server_id)
        if server is None:
            raise ValueError(f"server {server_id} not found")

        result_str = check_disk_handler({
            "host": server.host,
            "port": server.port,
            "username": server.username,
            "password": server.password,
            "command": command,
        })
        result = json.loads(result_str)

        finished_at = datetime.utcnow()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        if "error" in result:
            err_msg = result["error"]
            status = "ssh_error" if err_msg.startswith("SSH error") else "failed"
            stdout = ""
            stderr = err_msg
            exit_code = None
            structured = None
        else:
            status = "success"
            stdout = json.dumps(result, ensure_ascii=False)
            stderr = ""
            exit_code = 0
            structured = json.dumps(result, ensure_ascii=False)

        run = RunRecord(
            server_id=server_id,
            command=command,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            structured_result=structured,
            triggered_by=triggered_by,
            triggered_context=(
                json.dumps(triggered_context, ensure_ascii=False)
                if triggered_context is not None
                else None
            ),
        )
        session.add(run)

        if status == "success":
            server.last_seen_at = finished_at

        session.flush()
        return run


def persist_tool_run(
    server_id: int,
    command_label: str,
    result_json: str,
    triggered_by: str,
    triggered_context: Optional[dict] = None,
) -> RunRecord:
    """Persist the result of any tool handler (which returns a JSON string) as a
    RunRecord. Used by LLM wrappers (e.g. check_resources_on_server) so the
    audit log records both user-button runs and LLM-triggered tool calls.

    `command_label` is a short identifier (e.g. "check_resources", "list_services")
    that shows up in the History page — NOT an actual shell command.
    """
    started_at = datetime.utcnow()
    result = json.loads(result_json)
    finished_at = datetime.utcnow()
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)

    if "error" in result:
        err_msg = result["error"]
        status = "ssh_error" if err_msg.startswith("SSH error") else "failed"
        stdout = ""
        stderr = err_msg
        exit_code = None
        structured = None
    else:
        status = "success"
        stdout = json.dumps(result, ensure_ascii=False)
        stderr = ""
        exit_code = 0
        structured = json.dumps(result, ensure_ascii=False)

    with session_scope() as session:
        run = RunRecord(
            server_id=server_id,
            command=command_label,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            structured_result=structured,
            triggered_by=triggered_by,
            triggered_context=(
                json.dumps(triggered_context, ensure_ascii=False)
                if triggered_context is not None
                else None
            ),
        )
        session.add(run)

        if status == "success":
            server = session.get(Server, server_id)
            if server is not None:
                server.last_seen_at = finished_at

        session.flush()
        return run
