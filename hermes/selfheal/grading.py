"""自愈风险分级：确定性规则引擎（非 LLM）。

输出 {severity: ok|low|high, can_auto, reasons[]}。
severity=ok 表示无异常不处理；low 自主执行；high 需人工审批。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from . import config


def _is_peak_hour(now: datetime) -> bool:
    # 半开区间语义 [lo, hi)：含 lo 不含 hi（如 9-18 表示 9 时高峰、18 时非高峰）
    for lo, hi in config.PEAK_HOURS:
        if lo <= now.hour < hi:
            return True
    return False


def grade(
    scene: str,
    metrics: Optional[Dict[str, Any]],
    server: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now if now is not None else datetime.now()
    value = (metrics or {}).get("value")
    reasons = []
    severity = "ok"
    can_auto = False

    if scene == "disk_clean":
        if value is None:
            return {"severity": "ok", "can_auto": False, "reasons": ["磁盘探测无数据"]}
        if value >= config.DISK_HIGH_PCT:
            severity, can_auto = "high", False
            reasons.append(f"磁盘使用率 {value}% ≥ 高危阈值 {config.DISK_HIGH_PCT}%")
        elif value >= config.DISK_LOW_PCT:
            severity, can_auto = "low", True
            reasons.append(f"磁盘使用率 {value}% ≥ 低危阈值 {config.DISK_LOW_PCT}%")
        else:
            reasons.append(f"磁盘使用率 {value}% 低于低危阈值 {config.DISK_LOW_PCT}%")

    elif scene == "cache_clean":
        if value is None:
            return {"severity": "ok", "can_auto": False, "reasons": ["缓存探测无数据"]}
        if value >= config.CACHE_HIGH_PCT:
            severity, can_auto = "high", False
            reasons.append(f"缓存内存使用率 {value:.1f}% ≥ 高危阈值 {config.CACHE_HIGH_PCT}%")
        elif value >= config.CACHE_LOW_PCT:
            severity, can_auto = "low", True
            reasons.append(f"缓存内存使用率 {value:.1f}% ≥ 低危阈值 {config.CACHE_LOW_PCT}%")
        else:
            reasons.append(f"缓存内存使用率 {value:.1f}% 低于低危阈值 {config.CACHE_LOW_PCT}%")

    elif scene == "process_restart":
        if _is_peak_hour(now):
            severity, can_auto = "high", False
            reasons.append(f"当前为业务高峰期({now.hour}时)，重启服务需人工审批")
        else:
            severity, can_auto = "low", True
            reasons.append("服务异常且非业务高峰期，重启为低危操作")
    else:
        reasons.append(f"未知场景 {scene}")

    return {"severity": severity, "can_auto": can_auto, "reasons": reasons}
