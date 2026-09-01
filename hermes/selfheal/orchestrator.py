"""自愈编排器：探测→分级→执行/审批→验证→落库。

确定性流程，不依赖 LLM 自由发挥——成功率可测试可度量。
低危自主执行；高危落审批单等待人工批准；写命令只来自模板白名单。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from hermes.data import db
from hermes.data.models import SelfHealAction

from . import actions, config, detect, grading

logger = logging.getLogger(__name__)

SCENE_ACTION = {
    "process_restart": "restart_service",
    "disk_clean": "truncate_log",
    "cache_clean": "clean_cache",
    "log_cleanup_script": "run_cleanup_script",
}

# 清理类场景: 验证语义 = 磁盘使用率较执行前下降
CLEANUP_SCENES = ("log_cleanup_script", "ai_log_cleanup")

# 各场景 target 必填 key（缺失直接抛 ValueError，由顶层 catch 兜底落库）
REQUIRED_TARGET_KEYS = {
    "process_restart": ("service",),
    "disk_clean": ("mount", "path"),
    "cache_clean": ("mode",),
    "log_cleanup_script": ("category", "mount"),
}


def _probe_metric(scene: str, target: Dict[str, Any], out: str) -> Any:
    if scene == "disk_clean":
        return detect.parse_df_usage(out, target.get("mount", ""))
    if scene in CLEANUP_SCENES:
        return detect.parse_df_usage(out, target.get("mount", ""))
    if scene == "cache_clean":
        return detect.parse_meminfo(out).get("pct")
    return None


def verify_recovered(scene: str, target: Dict[str, Any], out: str,
                     baseline_pct: Optional[int] = None) -> bool:
    if scene in ("log_cleanup_script", "ai_log_cleanup"):
        pct = detect.parse_df_usage(out, target.get("mount", ""))
        if pct is None:
            return False
        if baseline_pct is None:
            return pct < config.DISK_LOW_PCT  # 无基线时退化为绝对阈值
        return pct < baseline_pct
    if scene == "process_restart":
        return detect.parse_systemctl_active(out)
    if scene == "disk_clean":
        pct = detect.parse_df_usage(out, target.get("mount", ""))
        return pct is not None and pct < config.DISK_LOW_PCT
    if scene == "cache_clean":
        pct = detect.parse_meminfo(out).get("pct")
        return pct is not None and pct < config.CACHE_LOW_PCT
    return False


def _validate_target(scene: str, target: Dict[str, Any]) -> None:
    for key in REQUIRED_TARGET_KEYS.get(scene, ()):
        if key not in target or target[key] in (None, ""):
            raise ValueError(f"scene '{scene}' 缺少必填 target key: {key}")


def run_selfheal(
    server_id: int,
    scene: str,
    target: Dict[str, Any],
    triggered_by: str = "user",
    action_name: Optional[str] = None,
) -> Dict[str, Any]:
    """执行一次自愈闭环。任何未预期异常兜底为 failed 并落库。"""
    try:
        return _run(server_id, scene, target, triggered_by, action_name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("自愈流程未预期异常: scene=%s server_id=%s", scene, server_id)
        try:
            _persist(server_id, scene, target, "high",
                     action_name or SCENE_ACTION.get(scene) or "unknown",
                     "failed", triggered_by,
                     execution={"error": str(exc)}, rendered_command=None)
        except Exception:  # noqa: BLE001
            logger.exception("自愈异常落库失败: scene=%s server_id=%s", scene, server_id)
        return {"status": "failed", "success": False, "reason": f"自愈流程异常: {exc}"}


def _run(
    server_id: int,
    scene: str,
    target: Dict[str, Any],
    triggered_by: str,
    action_name: Optional[str],
) -> Dict[str, Any]:
    action_name = action_name or SCENE_ACTION.get(scene)
    if action_name is None:
        raise ValueError(f"unknown scene: {scene}")
    _validate_target(scene, target)

    # ---- 1. 探测 ----
    probe_cmd = detect.probe_command(scene, target)
    probe = actions.exec_ssh(server_id, probe_cmd)
    if not probe["success"]:
        _persist(server_id, scene, target, "low", action_name, "failed",
                 triggered_by, execution={"error": probe.get("error")},
                 rendered_command=None)
        return {"severity": "low", "status": "failed", "success": False,
                "reason": f"探测失败: {probe.get('error')}"}

    metric = _probe_metric(scene, target, probe["stdout"])
    is_abnormal = (
        scene == "process_restart"
        and not detect.parse_systemctl_active(probe["stdout"])
    ) or (
        (scene == "disk_clean" or scene in CLEANUP_SCENES)
        and metric is not None and metric >= config.DISK_LOW_PCT
    ) or (
        scene == "cache_clean" and metric is not None
        and metric >= config.CACHE_LOW_PCT
    )
    if not is_abnormal:
        return {"severity": "ok", "status": "noop", "success": True,
                "reason": f"未检测到异常 (metric={metric})"}

    # ---- 2. 分级 ----
    g = grading.grade(scene, {"value": metric}, {"name": f"server-{server_id}"})

    # ---- 3. 低危自主 / 高危审批 ----
    if not g["can_auto"]:
        record = _persist(server_id, scene, target, g["severity"], action_name,
                          "pending", triggered_by, reasons=g["reasons"],
                          rendered_command=None)
        return {"severity": g["severity"], "status": "pending", "success": False,
                "action_id": record.id, "reasons": g["reasons"],
                "action_name": action_name,
                "message": "高危操作，已生成审批单等待人工批准"}

    # ---- 4. 渲染（模板白名单，全流程仅渲染一次） ----
    try:
        command = actions.render_command(action_name, target)
    except ValueError as exc:
        _persist(server_id, scene, target, g["severity"], action_name,
                 "failed", triggered_by, reasons=[str(exc)],
                 rendered_command=None)
        return {"severity": g["severity"], "status": "failed", "success": False,
                "reason": str(exc)}

    record = _persist(server_id, scene, target, g["severity"], action_name,
                      "executing", triggered_by, reasons=g["reasons"],
                      rendered_command=command)
    record = execute_and_verify(record, target, command)

    result = {
        "severity": g["severity"], "status": record.status,
        "success": record.success, "action_id": record.id,
        "action_name": action_name, "reasons": g["reasons"],
        "rendered_command": command,
    }
    if record.status == "failed" and record.execution_result:
        try:
            err = json.loads(record.execution_result).get("error")
        except (ValueError, TypeError, AttributeError):
            err = None
        result["reason"] = f"执行失败: {err}" if err else "执行失败"
    return result


def execute_and_verify(record, target, command, approver=None):
    """执行写命令并验证恢复(低危自主执行与人工审批共用)。

    record: 已落库的 SelfHealAction(含 id/server_id/scene/action_name)。
    command: 已渲染的白名单命令。
    approver: 审批人用户名,审批路径传入并记录 approver/approved_at。
    流程:
      0. 清理类场景记录执行前磁盘基线(供"较基线下降"验证语义);
      1. 经 exec_action 执行写命令(支持脚本 stdin 注入),失败置 failed;
      2. 用 detect.probe_command 探测 + verify_recovered 判定 →
         verified(success=True)/verification_failed(success=False)。
    返回更新后的 record(在打开的 session 内读回)。
    """
    action_id = record.id
    server_id = record.server_id
    scene = record.scene

    # ---- 0. 清理类场景: 记录执行前磁盘基线 ----
    baseline_pct = None
    if scene in CLEANUP_SCENES:
        probe_cmd = detect.probe_command(scene, target)
        probe = actions.exec_ssh(server_id, probe_cmd)
        if probe.get("success"):
            baseline_pct = detect.parse_df_usage(probe.get("stdout", ""), target.get("mount", ""))

    # ---- 1. 执行写命令(经 exec_action 支持脚本注入) ----
    exec_result = actions.exec_action(server_id, record.action_name, command, target)
    if not exec_result["success"]:
        with db.session_scope() as s:
            row = s.get(SelfHealAction, action_id)
            row.status = "failed"
            row.success = False
            row.rendered_command = command
            row.execution_result = json.dumps(exec_result, ensure_ascii=False)
            row.executed_at = datetime.now(timezone.utc)
            if approver is not None:
                row.approver = approver
                row.approved_at = datetime.now(timezone.utc)
            return row

    with db.session_scope() as s:
        row = s.get(SelfHealAction, action_id)
        row.status = "executed"
        row.rendered_command = command
        row.execution_result = json.dumps(exec_result, ensure_ascii=False)
        row.executed_at = datetime.now(timezone.utc)
        if approver is not None:
            row.approver = approver
            row.approved_at = datetime.now(timezone.utc)

    # ---- 2. 验证(复用探测命令) ----
    verify_cmd = detect.probe_command(scene, target)
    verify = actions.exec_ssh(server_id, verify_cmd)
    recovered = bool(verify.get("success")) and verify_recovered(
        scene, target, verify.get("stdout", ""), baseline_pct)

    with db.session_scope() as s:
        row = s.get(SelfHealAction, action_id)
        row.status = "verified" if recovered else "verification_failed"
        row.success = recovered
        row.verification_result = json.dumps(verify, ensure_ascii=False)
        return row


def _persist(server_id, scene, target, severity, action_name, status,
             triggered_by, reasons=None, execution=None, rendered_command=None):
    with db.session_scope() as s:
        row = SelfHealAction(
            server_id=server_id, scene=scene,
            target=json.dumps(target, ensure_ascii=False),
            severity=severity, action_name=action_name,
            rendered_command=rendered_command, status=status,
            triggered_by=triggered_by,
            execution_result=json.dumps(execution, ensure_ascii=False) if execution else None,
            grade_reasons=json.dumps(reasons, ensure_ascii=False) if reasons else None,
        )
        s.add(row)
        s.flush()
        return row
