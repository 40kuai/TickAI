"""自愈探测层：每场景一个硬编码只读命令 + 纯解析函数。

探测命令不接受外部拼接（target 参数化后格式化为固定模板），
解析函数均为纯函数、可单测。写操作绝不在此发生。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# ---- 探测命令模板（硬编码只读） ----
def probe_command(scene: str, target: Dict[str, Any]) -> str:
    """按场景返回硬编码只读探测命令。target 仅参数化模板。"""
    if scene == "process_restart":
        service = target["service"]
        return f"systemctl is-active {service}"
    if scene == "disk_clean":
        mount = target["mount"]
        return f"df -Th {mount}"
    if scene in ("log_cleanup_script", "ai_log_cleanup"):
        mount = target.get("mount") or "/"
        return f"df -Th {mount}"
    if scene == "cache_clean":
        return "cat /proc/meminfo"
    raise ValueError(f"unknown scene: {scene}")


# ---- 纯解析函数 ----
def parse_systemctl_active(output: str) -> bool:
    """systemctl is-active 输出 → 服务是否 active。"""
    line = (output or "").strip().splitlines()
    return bool(line) and line[0].strip().lower() == "active"


def parse_df_usage(df_th_output: str, mount: str) -> Optional[int]:
    """解析 `df -Th <mount>` 输出，返回目标挂载点使用率百分比。"""
    mount = mount.rstrip("/") or "/"
    for raw in (df_th_output or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("Filesystem"):
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        use_pct_str = parts[5]
        mnt = parts[6]
        if (mnt.rstrip("/") or "/") != mount:
            continue
        try:
            return int(use_pct_str.rstrip("%"))
        except ValueError:
            return None
    return None


def parse_meminfo(meminfo_output: str) -> Dict[str, Any]:
    """解析 `/proc/meminfo` → 缓存占比。

    缓存 = Buffers + Cached + SReclaimable(可回收 slab)。
    pct = cache_kb / mem_total_kb * 100；无法计算时为 None。
    """
    total = 0
    buffers = 0
    cached = 0
    reclaimable = 0
    for line in (meminfo_output or "").splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        parts = val.split()
        num = _safe_int(parts[0]) if parts else 0
        if key == "MemTotal":
            total = num
        elif key == "Buffers":
            buffers = num
        elif key == "Cached":
            cached = num
        elif key == "SReclaimable":
            reclaimable = num
    cache_kb = buffers + cached + reclaimable
    pct = (cache_kb / total * 100) if total > 0 and cache_kb > 0 else None
    return {"mem_total": total, "cache_kb": cache_kb, "pct": pct}


def _safe_int(raw: str) -> int:
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return 0
