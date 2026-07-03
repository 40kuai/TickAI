"""System Tool — read-only CPU/memory/process/service inspection over SSH.

Two handlers, both strictly read-only:

  - check_resources  → load, memory, top CPU processes
  - list_services    → systemd-managed services + abnormal flag

Same safety model as tools/disk.py:
  - No arbitrary command execution. The user (or LLM) can only pass
    host/username/password/port. The actual shell commands are hardcoded
    constants here and cannot be influenced by the caller.
  - try/finally guarantees transport.close() on every code path.
  - 10-second timeout per exec_command.
  - No SFTP subsystem opened.
"""
import json
import re
from typing import Any, Dict, List, Optional

import paramiko

from hermes.tools.registry import registry, tool_error, tool_result


# ============================================================
# Hardcoded commands (NEVER accept from user)
# ============================================================

# One script that prints four delimited sections, pipe-limited to avoid
# runaway output on busy servers. Each section is tagged so the parser
# knows what it's looking at. We use /proc/meminfo (always kB) instead of
# `free -m` (format varies across distros) for parser stability.
RESOURCES_CMD = (
    "{ echo '===UPTIME==='; uptime; "
    "echo '===NPROC==='; nproc; "
    "echo '===FREE==='; cat /proc/meminfo; "
    "echo '===TOP==='; ps -e -o pid,user,pcpu,pmem,args "
    "--sort=-pcpu --no-headers; } | head -80"
)

# List ALL service units, with state + sub-state + description. We strip the
# leading "UNIT LOAD ACTIVE SUB DESCRIPTION" header by filtering on lines
# that look like a unit name (end in .service).
SERVICES_CMD = "systemctl list-units --type=service --all --no-pager --no-legend"


# ============================================================
# Schemas
# ============================================================

RESOURCES_SCHEMA = {
    "name": "check_resources",
    "description": (
        "Read-only CPU/memory/process inspection of a remote Linux host via SSH. "
        "Returns load averages (1/5/15 min), CPU core count, memory and swap usage, "
        "and the top CPU-consuming processes. NEVER modifies the server.\n\n"
        "Output includes a `pressure_level` field ('low'/'medium'/'high') and "
        "`pressure_reasons` listing which metrics triggered that level. Use these "
        "to prioritize which server needs attention.\n\n"
        "When diagnosing root causes, look at `top_processes` first:\n"
        "  - mysqld/postgres on top + high load + mem pressure → DB load\n"
        "  - java/python with high mem + low CPU → GC pressure or memory leak\n"
        "  - kswapd/0 on top + low CPU + high swap → swap thrashing\n"
        "  - nginx/httpd on top + high load → request surge\n\n"
        "After diagnosis, recommend optimization (e.g., increase connection pool, "
        "scale up, add cache, fix slow query). Do NOT suggest restarting services "
        "from this tool — that requires a separate user-confirmed action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Server IP or hostname."},
            "username": {"type": "string", "description": "SSH username."},
            "password": {"type": "string", "description": "SSH password."},
            "port": {"type": "integer", "default": 22, "minimum": 1, "maximum": 65535},
        },
        "required": ["host", "username", "password"],
    },
}

SERVICES_SCHEMA = {
    "name": "list_services",
    "description": (
        "Read-only enumeration of systemd-managed services on a remote Linux host. "
        "Returns each service's name, state (active/inactive/failed), "
        "sub-state (running/exited/dead/...), and an `is_abnormal` flag.\n\n"
        "A service is considered abnormal when:\n"
        "  - state is not 'active' (e.g., 'failed', 'inactive'), OR\n"
        "  - state is 'active' but sub_state is not in {running, exited, waiting}\n\n"
        "Sub-state 'exited' is normal for one-shot services (cron, logrotate). "
        "Sub-state 'waiting' is normal for socket-activated services.\n\n"
        "This tool NEVER starts, stops, or modifies services. To restart a service, "
        "the user must explicitly do so via the UI — recommend it instead of doing it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Server IP or hostname."},
            "username": {"type": "string", "description": "SSH username."},
            "password": {"type": "string", "description": "SSH password."},
            "port": {"type": "integer", "default": 22, "minimum": 1, "maximum": 65535},
        },
        "required": ["host", "username", "password"],
    },
}


# ============================================================
# Connection helpers (same shape as tools/disk.py)
# ============================================================

def _create_ssh_client(host: str, port: int, username: str, password: str) -> paramiko.SSHClient:
    """Create a connected SSHClient. Caller is responsible for .close()."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=10,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def _close_ssh(client) -> None:
    """Best-effort close. Never raises."""
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def _validate_conn_args(args: dict) -> Optional[str]:
    """Return error message if invalid, None if OK."""
    if not args.get("host"):
        return "host is required"
    if not args.get("username"):
        return "username is required"
    if not args.get("password"):
        return "password is required"
    port = args.get("port", 22)
    try:
        port = int(port)
    except (TypeError, ValueError):
        return f"port must be an integer, got {port!r}"
    if not (1 <= port <= 65535):
        return f"port must be 1-65535, got {port}"
    return None


# ============================================================
# Pure parsers (testable without SSH)
# ============================================================

_LOAD_RE = re.compile(r"load average:\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)")


def parse_loadavg(uptime_stdout: str) -> List[float]:
    """Extract [load_1, load_5, load_15] from `uptime` output."""
    m = _LOAD_RE.search(uptime_stdout)
    if not m:
        return [0.0, 0.0, 0.0]
    return [float(m.group(1)), float(m.group(2)), float(m.group(3))]


_MEM_LINE_RE = re.compile(r"^(\w+):\s+(\d+)\s*kB$")


def parse_meminfo(meminfo_stdout: str) -> Dict[str, Any]:
    """Parse /proc/meminfo (kB form) into structured dict.

    Modern Linux formula: used = total - MemAvailable (the kernel already
    accounts for reclaimable buffers/cached in MemAvailable, so we must
    NOT subtract them again — that would double-count).
    """
    info: Dict[str, int] = {}
    for line in meminfo_stdout.splitlines():
        m = _MEM_LINE_RE.match(line.strip())
        if not m:
            continue
        info[m.group(1)] = int(m.group(2))

    def mb(v: int) -> int:
        return v // 1024 if v else 0

    total_kb = info.get("MemTotal", 0)
    avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb = max(0, total_kb - avail_kb) if total_kb else 0

    swap_total_kb = info.get("SwapTotal", 0)
    swap_free_kb = info.get("SwapFree", 0)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)

    return {
        "total_mb": mb(total_kb),
        "used_mb": mb(used_kb),
        "avail_mb": mb(avail_kb),
        "use_pct": round(used_kb * 100 / total_kb) if total_kb else 0,
        "swap_total_mb": mb(swap_total_kb),
        "swap_used_mb": mb(swap_used_kb),
        "swap_use_pct": round(swap_used_kb * 100 / swap_total_kb) if swap_total_kb else 0,
    }


def parse_top_processes(top_stdout: str) -> List[Dict[str, Any]]:
    """Parse `ps -e -o pid,user,pcpu,pmem,args ...` output."""
    procs: List[Dict[str, Any]] = []
    for line in top_stdout.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[2])
            mem = float(parts[3])
        except ValueError:
            continue
        procs.append({
            "pid": pid,
            "user": parts[1],
            "cpu_pct": cpu,
            "mem_pct": mem,
            "command": parts[4],
        })
    return procs


def parse_services_output(systemctl_stdout: str) -> List[Dict[str, Any]]:
    """Parse `systemctl list-units --no-legend` output.

    Each non-blank line: <name.service>  <load>  <state>  <sub>  <description...>

    Abnormal if state != 'active' OR sub not in {running, exited, waiting}.
    """
    normal_subs = {"running", "exited", "waiting"}
    services: List[Dict[str, Any]] = []
    for line in systemctl_stdout.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit = parts[0]
        if not unit.endswith(".service"):
            continue
        state = parts[2]
        sub = parts[3]
        description = parts[4] if len(parts) > 4 else ""
        is_abnormal = (state != "active") or (sub not in normal_subs)
        services.append({
            "name": unit[: -len(".service")],
            "state": state,
            "sub_state": sub,
            "is_abnormal": is_abnormal,
            "description": description,
        })
    return services


def compute_pressure_level(load_per_core: float, mem_pct: int) -> str:
    """Heuristic classifier.

    low    — load_per_core < 0.7 AND mem < 80
    medium — load_per_core < 1.5 AND mem < 90
    high   — anything else
    """
    if load_per_core < 0.7 and mem_pct < 80:
        return "low"
    if load_per_core < 1.5 and mem_pct < 90:
        return "medium"
    return "high"


# ============================================================
# Handlers
# ============================================================

def check_resources_handler(args: dict, **kwargs) -> str:
    err = _validate_conn_args(args)
    if err:
        return tool_error(err)

    host = args["host"]
    port = int(args.get("port", 22))
    username = args["username"]
    password = args["password"]

    client = None
    try:
        client = _create_ssh_client(host, port, username, password)
        stdin, stdout, stderr = client.exec_command(RESOURCES_CMD, timeout=10)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err_text = stderr.read().decode("utf-8", errors="replace")
            return tool_error(f"command failed (exit {exit_code}): {err_text.strip()}")

        raw = stdout.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return tool_error(f"SSH error: {exc}")
    finally:
        _close_ssh(client)

    # Split sections by markers
    sections: Dict[str, str] = {}
    current = None
    buf: List[str] = []
    for line in raw.splitlines():
        if line.startswith("===") and line.endswith("==="):
            if current is not None:
                sections[current] = "\n".join(buf)
            current = line.strip("=").strip()
            buf = []
        else:
            if current is not None:
                buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)

    load = parse_loadavg(sections.get("UPTIME", ""))
    try:
        cores = int(sections.get("NPROC", "1").strip().splitlines()[0])
    except (ValueError, IndexError):
        cores = 1
    mem = parse_meminfo(sections.get("FREE", ""))
    procs = parse_top_processes(sections.get("TOP", ""))

    load_per_core = round(load[0] / cores, 2) if cores else load[0]
    pressure = compute_pressure_level(load_per_core, mem["use_pct"])

    reasons: List[str] = []
    if load_per_core >= 1.5:
        reasons.append(f"load_1 per-core is {load_per_core} (>= 1.5)")
    elif load_per_core >= 0.7:
        reasons.append(f"load_1 per-core is {load_per_core} (>= 0.7)")
    if mem["use_pct"] >= 90:
        reasons.append(f"memory use is {mem['use_pct']}% (>= 90%)")
    elif mem["use_pct"] >= 80:
        reasons.append(f"memory use is {mem['use_pct']}% (>= 80%)")
    if mem["swap_use_pct"] >= 30:
        reasons.append(f"swap use is {mem['swap_use_pct']}% (>= 30%) — possible memory pressure")
    if not reasons:
        reasons.append("all metrics within healthy range")

    return tool_result(
        load_1_5_15=load,
        load_per_core_1=load_per_core,
        cpu_cores=cores,
        memory=mem,
        top_processes=procs[:10],
        pressure_level=pressure,
        pressure_reasons=reasons,
    )


def list_services_handler(args: dict, **kwargs) -> str:
    err = _validate_conn_args(args)
    if err:
        return tool_error(err)

    host = args["host"]
    port = int(args.get("port", 22))
    username = args["username"]
    password = args["password"]

    client = None
    try:
        client = _create_ssh_client(host, port, username, password)
        stdin, stdout, stderr = client.exec_command(SERVICES_CMD, timeout=10)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err_text = stderr.read().decode("utf-8", errors="replace")
            return tool_error(f"systemctl failed (exit {exit_code}): {err_text.strip()}")

        raw = stdout.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return tool_error(f"SSH error: {exc}")
    finally:
        _close_ssh(client)

    services = parse_services_output(raw)
    abnormal_count = sum(1 for s in services if s["is_abnormal"])
    return tool_result(
        total=len(services),
        abnormal=abnormal_count,
        services=services,
    )


# ============================================================
# Registration
# ============================================================

def _always_available() -> bool:
    return True


registry.register(
    name="check_resources",
    toolset="system",
    schema=RESOURCES_SCHEMA,
    handler=check_resources_handler,
    check_fn=_always_available,
    emoji="📊",
    max_result_size_chars=8000,
)

registry.register(
    name="list_services",
    toolset="system",
    schema=SERVICES_SCHEMA,
    handler=list_services_handler,
    check_fn=_always_available,
    emoji="⚙️",
    max_result_size_chars=16000,
)
