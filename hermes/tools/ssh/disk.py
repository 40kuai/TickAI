"""Disk Tool — check disk usage on a remote Linux server via SSH.

DESIGN GOAL: ZERO side-effects on the remote server.

Guarantees (enforced in code, not just docs):
  • Only `df`, `df -h`, `df -Th` can ever run — hardcoded allowlist
  • Dangerous shell metacharacters are rejected even within allowlisted commands
  • Command validation runs BEFORE any network call
  • SFTP subsystem is never opened (no upload/download possible)
  • No local temp files; output parsed in memory only
  • transport.close() runs in finally — connection is always torn down
  • No remote scripts, no remote writes, no agents, no forwards
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

import paramiko

from hermes.tools.registry import registry, tool_error, tool_result


# ============================================================
# 1. Safety: command allowlist + dangerous character filter
# ============================================================
ALLOWED_COMMANDS = {"df -Th", "df -h", "df"}
DANGEROUS_CHARS = re.compile(r"[;&|`$<>\n\r]")


def validate_command(command: str) -> None:
    """Reject anything that isn't in the allowlist or contains shell metachars.

    Raises ValueError on rejection. Caller should turn that into a tool_error.
    """
    if not command or not command.strip():
        raise ValueError("command cannot be empty")
    if command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"command not allowed: {command!r}. "
            f"Allowed: {sorted(ALLOWED_COMMANDS)}"
        )
    if DANGEROUS_CHARS.search(command):
        raise ValueError(f"command contains dangerous characters: {command!r}")


# ============================================================
# 2. Pure parser: `df -Th` text → structured records
# ============================================================
WARNING_THRESHOLD = 80
CRITICAL_THRESHOLD = 90


def parse_df_output(text: str) -> List[Dict[str, Any]]:
    """Parse `df -Th` output into structured records.

    Each record: filesystem, type, size, used, avail, use_pct, mount, warning, critical.
    Records are sorted by use_pct descending so the most concerning ones surface first.
    """
    mounts: List[Dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Filesystem"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        # df -Th columns: Filesystem Type Size Used Avail Use% Mounted on
        filesystem, ftype, size, used, avail, use_pct_str = parts[:6]
        mount = parts[6] if len(parts) > 6 else ""
        try:
            pct = int(use_pct_str.rstrip("%"))
        except ValueError:
            continue
        mounts.append({
            "filesystem": filesystem,
            "type": ftype,
            "size": size,
            "used": used,
            "avail": avail,
            "use_pct": pct,
            "mount": mount,
            "warning": pct >= WARNING_THRESHOLD,
            "critical": pct >= CRITICAL_THRESHOLD,
        })
    mounts.sort(key=lambda m: m["use_pct"], reverse=True)
    return mounts


# ============================================================
# 3. SSH transport — wrapped so the factory is mockable in tests
# ============================================================
def _create_ssh_client() -> paramiko.SSHClient:
    """Build a configured SSHClient. Wrapped so tests can patch it."""
    client = paramiko.SSHClient()
    # AutoAddPolicy is convenient for a demo; production should pin a known_hosts file
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def _close_ssh(client: paramiko.SSHClient) -> None:
    """Best-effort cleanup. Never raises."""
    if client is None:
        return
    try:
        transport = client.get_transport()
        if transport is not None:
            transport.close()
    except Exception:
        pass
    try:
        client.close()
    except Exception:
        pass


# ============================================================
# 4. Handler
# ============================================================
def check_disk_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    """Check disk usage on a remote server via SSH. Read-only on the remote.

    Required: host, username, password
    Optional: port (default 22), command (default "df -Th")

    Returns JSON with `mounts` (sorted by use_pct desc) and `summary`.
    """
    # ---- 4.1 Argument validation (BEFORE any network call) ----
    host = args.get("host", "")
    if not isinstance(host, str) or not host.strip():
        return tool_error("host is required")
    host = host.strip()

    username = args.get("username", "")
    if not isinstance(username, str) or not username.strip():
        return tool_error("username is required")
    username = username.strip()

    password = args.get("password", "")
    if not isinstance(password, str) or not password:
        return tool_error("password is required")

    port_raw = args.get("port", 22)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return tool_error(f"port must be an integer, got {port_raw!r}")
    if not (1 <= port <= 65535):
        return tool_error(f"port must be 1-65535, got {port}")

    command = args.get("command", "df -Th")
    if not isinstance(command, str):
        return tool_error("command must be a string")

    # ---- 4.2 Command safety (BEFORE any network call) ----
    try:
        validate_command(command)
    except ValueError as exc:
        return tool_error(f"command rejected: {exc}")

    # ---- 4.3 Connect, run, parse — ALWAYS close in finally ----
    client = _create_ssh_client()
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=10,
            allow_agent=False,    # don't use the local ssh-agent
            look_for_keys=False,  # don't try ~/.ssh/id_* — password only
        )
        # SFTP subsystem is intentionally NEVER opened
        stdin, stdout, stderr = client.exec_command(command, timeout=10)
        output_bytes = stdout.read()
        err_bytes = stderr.read()
        exit_code = stdout.channel.recv_exit_status()

        if exit_code != 0:
            err_text = err_bytes.decode("utf-8", errors="replace").strip()[:200]
            return tool_error(f"remote command failed (exit={exit_code}): {err_text}")

        output = output_bytes.decode("utf-8", errors="replace")
        mounts = parse_df_output(output)
        return tool_result(
            mounts=mounts,
            summary={
                "total_mounts": len(mounts),
                "warning_count": sum(1 for m in mounts if m["warning"]),
                "critical_count": sum(1 for m in mounts if m["critical"]),
            },
        )
    except Exception as exc:  # noqa: BLE001 — any failure becomes a tool error
        return tool_error(f"SSH error: {type(exc).__name__}: {exc}")
    finally:
        _close_ssh(client)


# ============================================================
# 5. Schema — what the LLM sees
# ============================================================
DISK_SCHEMA = {
    "name": "check_disk_usage",
    "description": (
        "Check disk usage on a remote Linux server via SSH (password auth). "
        "Connects, runs `df -Th` (read-only), parses the output into structured "
        "data, and returns mount points sorted by usage percent. The remote "
        "server is left completely untouched — no files written, no processes "
        "left behind, connection always closed.\n\n"
        "Use when the user wants to check disk space on a server.\n\n"
        "Examples:\n"
        "  check_disk_usage(host='1.2.3.4', username='root', password='s3cret')\n"
        "  check_disk_usage(host='srv.local', username='admin', password='pw', port=2222)\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "host": {
                "type": "string",
                "description": "Hostname or IP address of the SSH server.",
            },
            "username": {
                "type": "string",
                "description": "SSH username.",
            },
            "password": {
                "type": "string",
                "description": (
                    "SSH password. Passed in plaintext to the LLM — use only on "
                    "trusted, local-network machines for this demo."
                ),
            },
            "port": {
                "type": "integer",
                "description": "SSH port. Default 22.",
                "default": 22,
                "minimum": 1,
                "maximum": 65535,
            },
            "command": {
                "type": "string",
                "description": (
                    "Command to run on the remote. Hardcoded allowlist: only "
                    "'df -Th', 'df -h', 'df' are accepted."
                ),
                "default": "df -Th",
            },
        },
        "required": ["host", "username", "password"],
    },
}


def check_disk_requirements() -> bool:
    """Check that paramiko is importable."""
    try:
        import paramiko  # noqa: F401
        return True
    except ImportError:
        return False


registry.register(
    name="check_disk_usage",
    toolset="system",
    schema=DISK_SCHEMA,
    handler=check_disk_handler,
    check_fn=check_disk_requirements,
    emoji="💾",
    max_result_size_chars=8000,
)
