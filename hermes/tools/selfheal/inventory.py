"""scan_log_cleanup — 只读日志/磁盘/容器清单扫描(通道二 AI 分析数据源).

确定性 SSH 扫描, 输出结构化 JSON 供 AI 策略 skill 分析。零副作用:
所有命令硬编码只读, 复用 hermes.selfheal.actions.exec_ssh(凭据隔离)。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from hermes.selfheal import actions
from hermes.tools.registry import registry, tool_error, tool_result

# 硬编码只读探测命令(顺序固定, 解析按序消费)
_PROBE_COMMANDS = [
    ("df", "df -Th /"),
    ("journal", "journalctl --disk-usage 2>/dev/null || true"),
    ("var_log", "find /var/log -maxdepth 2 -type f -size +50M -printf '%s %p\\n' 2>/dev/null | sort -rn | head -50"),
    ("service", "find /data -maxdepth 3 -type f -path '*/logs/*' -size +50M -printf '%s %p\\n' 2>/dev/null | sort -rn | head -50"),
    ("docker_log", "find /var/lib/docker/containers -maxdepth 2 -name '*-json.log' -size +50M -printf '%s %p\\n' 2>/dev/null | sort -rn | head -50"),
    ("docker_ps", "docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}' 2>/dev/null | head -50"),
    ("docker_images", "docker images --format '{{.Repository}} {{.Tag}} {{.ID}} {{.CreatedAt}} {{.Size}}' 2>/dev/null | head -50"),
]

DANGLING_IMAGE_MARKERS = ("<none>", "None")


def _build_probe_commands() -> List[str]:
    return [cmd for _, cmd in _PROBE_COMMANDS]


def _parse_df(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("Filesystem"):
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        try:
            use_pct = int(parts[5].rstrip("%"))
        except ValueError:
            continue
        out[parts[6]] = {"use_pct": use_pct, "used": parts[3], "avail": parts[4]}
    return out


def _parse_journal(text: str) -> Dict[str, Any]:
    import re
    m = re.search(r"take up\s+([\d.]+[GMK]?)\s+in the file system", text or "")
    return {"disk_used": m.group(1) if m else None}


def _parse_file_list(text: str, limit: int = 20) -> List[Dict[str, Any]]:
    """解析 'find -printf "%s %p\\n"' 输出 → [{path, size_mb}]，按大小倒序。"""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            size_mb = round(int(parts[0]) / 1024 / 1024, 1)
        except ValueError:
            continue
        out.append({"path": parts[1], "size_mb": size_mb})
        if len(out) >= limit:
            break
    return out


def _parse_docker_ps(text: str) -> List[Dict[str, Any]]:
    out = []
    for raw in (text or "").splitlines():
        parts = raw.split()
        if len(parts) >= 3:
            out.append({"name": parts[0], "image": parts[1], "status": parts[2]})
    return out


def _parse_docker_images(text: str) -> Dict[str, Any]:
    total = 0
    dangling = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("REPOSITORY"):
            continue
        parts = line.split()
        if not parts:
            continue
        total += 1
        if parts[0] in DANGLING_IMAGE_MARKERS:
            dangling += 1
    return {"total": total, "dangling": dangling}


def _parse_inventory(raw: Dict[str, str]) -> Dict[str, Any]:
    return {
        "disk": _parse_df(raw.get("df", "")),
        "journal": _parse_journal(raw.get("journal", "")),
        "var_log": _parse_file_list(raw.get("var_log", "")),
        "service_logs": _parse_file_list(raw.get("service", "")),
        "docker_logs": _parse_file_list(raw.get("docker_log", "")),
        "docker_containers": _parse_docker_ps(raw.get("docker_ps", "")),
        "docker_images": _parse_docker_images(raw.get("docker_images", "")),
    }


def _scan_schema() -> Dict[str, Any]:
    return {
        "name": "scan_log_cleanup",
        "description": (
            "只读扫描服务器的日志/磁盘/容器现状(磁盘使用、journald、/var/log 大文件、"
            "/data 服务日志、docker 容器日志、容器与镜像)。返回结构化清单供 AI 分析清理策略。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "integer",
                    "description": "目标服务器 ID(必填, 来自 list_servers)。",
                },
            },
            "required": ["server_id"],
        },
    }


def scan_log_cleanup_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    args = args or {}
    server_id = args.get("server_id")
    if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id <= 0:
        return tool_error(f"server_id 必须是正整数, 收到: {server_id!r}")

    raw: Dict[str, str] = {}
    for key, cmd in _PROBE_COMMANDS:
        r = actions.exec_ssh(server_id, cmd, timeout=30)
        if not r["success"]:
            return tool_error(f"扫描失败({key}): {r.get('error')}")
        raw[key] = r.get("stdout", "")

    inventory = _parse_inventory(raw)
    inventory["server_id"] = server_id
    return tool_result(**inventory)


def scan_log_cleanup_available() -> bool:
    return True


registry.register(
    name="scan_log_cleanup",
    toolset="system",
    schema=_scan_schema(),
    handler=scan_log_cleanup_handler,
    check_fn=scan_log_cleanup_available,
    emoji="🔍",
    max_result_size_chars=20000,
)
