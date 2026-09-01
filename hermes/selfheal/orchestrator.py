"""自愈编排器：探测→分级→执行/审批→验证→落库。

确定性流程，不依赖 LLM 自由发挥——成功率可测试可度量。
低危自主执行；高危落审批单等待人工批准；写命令只来自模板白名单。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from hermes.data import db
from hermes.data.models import SelfHealAction
from hermes.tools.ssh import get_server_ssh_args

from . import actions, detect, grading

logger = logging.getLogger(__name__)

SCENE_ACTION = {
    "process_restart": "restart_service",
    "disk_clean": "truncate_log",
    "cache_clean": "clean_cache",
}

SCENE_VERIFY_PROBE = {
    "process_restart": "systemctl is-active {service}",
    "disk_clean": "df -Th {mount}",
    "cache_clean": "cat /proc/meminfo",
}


def _probe_metric(scene: str, target: Dict[str, Any], out: str) -> Any:
    if scene == "process_restart":
        return None
    if scene == "disk_clean":
        return detect.parse_df_usage(out, target.get("mount", ""))
    if scene == "cache_clean":
        return detect.parse_meminfo(out).get("pct")
    return None


def _verify_recovered(scene: str, target: Dict[str, Any], out: str) -> bool:
    from . import config
    if scene == "process_restart":
        return detect.parse_systemctl_active(out)
    if scene == "disk_clean":
        pct = detect.parse_df_usage(out, target.get("mount", ""))
        return pct is not None and pct < config.DISK_LOW_PCT
    if scene == "cache_clean":
        pct = detect.parse_meminfo(out).get("pct")
        return pct is not None and pct < config.CACHE_LOW_PCT
    return False


def run_selfheal(
    server_id: int,
    scene: str,
    target: Dict[str, Any],
    triggered_by: str = "user",
    action_name: Optional[str] = None,
) -> Dict[str, Any]:
    action_name = action_name or SCENE_ACTION.get(scene)
    if action_name is None:
        raise ValueError(f"unknown scene: {scene}")

    # ---- 1. 探测 ----
    probe_cmd = detect.probe_command(scene, target)
    probe = actions.exec_ssh(server_id, probe_cmd)
    if not probe["success"]:
        _persist(server_id, scene, target, "low", action_name, "failed",
                 triggered_by, execution={"error": probe.get("error")})
        return {"severity": "low", "status": "failed", "success": False,
                "reason": f"探测失败: {probe.get('error')}"}

    metric = _probe_metric(scene, target, probe["stdout"])
    is_abnormal = (
        scene == "process_restart" and not detect.parse_systemctl_active(probe["stdout"])
    ) or (
        scene in ("disk_clean", "cache_clean") and metric is not None
        and metric >= 80
    )
    if not is_abnormal:
        return {"severity": "ok", "status": "noop", "success": True,
                "reason": f"未检测到异常 (metric={metric})"}

    # ---- 2. 分级 ----
    g = grading.grade(scene, {"value": metric}, {"name": f"server-{server_id}"})

    # ---- 3. 低危自主 / 高危审批 ----
    if not g["can_auto"]:
        record = _persist(server_id, scene, target, g["severity"], action_name,
                          "pending", triggered_by, reasons=g["reasons"])
        return {"severity": g["severity"], "status": "pending", "success": False,
                "action_id": record.id, "reasons": g["reasons"],
                "action_name": action_name,
                "message": "高危操作，已生成审批单等待人工批准"}

    # ---- 4. 执行（模板白名单） ----
    try:
        command = actions.render_command(action_name, target)
    except ValueError as exc:
        _persist(server_id, scene, target, g["severity"], action_name,
                 "failed", triggered_by, reasons=[str(exc)])
        return {"severity": g["severity"], "status": "failed", "success": False,
                "reason": str(exc)}

    record = _persist(server_id, scene, target, g["severity"], action_name,
                      "executing", triggered_by, reasons=g["reasons"])
    exec_result = actions.exec_ssh(server_id, command)
    if not exec_result["success"]:
        record.status = "failed"
        record.execution_result = json.dumps(exec_result, ensure_ascii=False)
        record.executed_at = datetime.utcnow()
        _save(record)
        return {"severity": g["severity"], "status": "failed", "success": False,
                "reason": f"执行失败: {exec_result.get('error')}"}

    record.status = "executed"
    record.execution_result = json.dumps(exec_result, ensure_ascii=False)
    record.executed_at = datetime.utcnow()
    _save(record)

    # ---- 5. 验证（复用探测） ----
    verify_cmd = SCENE_VERIFY_PROBE[scene].format(**target)
    verify = actions.exec_ssh(server_id, verify_cmd)
    if verify["success"] and _verify_recovered(scene, target, verify["stdout"]):
        record.status = "verified"
        record.success = True
    else:
        record.status = "verification_failed"
        record.success = False
    record.verification_result = json.dumps(verify, ensure_ascii=False)
    _save(record)

    return {
        "severity": g["severity"], "status": record.status,
        "success": record.success, "action_id": record.id,
        "action_name": action_name, "reasons": g["reasons"],
        "rendered_command": command,
    }


def _persist(server_id, scene, target, severity, action_name, status,
             triggered_by, reasons=None, execution=None):
    command = None
    try:
        command = actions.render_command(action_name, target)
    except ValueError:
        command = ""
    with db.session_scope() as s:
        row = SelfHealAction(
            server_id=server_id, scene=scene,
            target=json.dumps(target, ensure_ascii=False),
            severity=severity, action_name=action_name,
            rendered_command=command, status=status,
            triggered_by=triggered_by,
            execution_result=json.dumps(execution, ensure_ascii=False) if execution else None,
            verification_result=json.dumps(reasons, ensure_ascii=False) if reasons else None,
        )
        s.add(row)
        s.flush()
        return row


def _save(record: SelfHealAction) -> None:
    with db.session_scope() as s:
        s.merge(record)
