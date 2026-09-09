"""自愈编排器：探测→冷却→影响面采集→统一审批出口→执行/审批→验证→落库。

确定性流程，不依赖 LLM 自由发挥——成功率可测试可度量。
触发(检测异常)与审批(操作影响风险)解耦: 统一经 approval.decide 三态
auto/approval/reject 决策; 写命令只来自模板白名单。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from hermes.data import db
from hermes.data.models import SelfHealAction

from . import actions, approval, config, detect, run_cleanup

logger = logging.getLogger(__name__)

SCENE_ACTION = {
    "process_restart": "restart_service",
    "disk_clean": "truncate_log",
    "cache_clean": "clean_cache",
    "log_cleanup_script": "run_cleanup_script",
}

# 清理类场景: 验证语义 = 清理后须降到安全水位 DISK_LOW_PCT 以下
CLEANUP_SCENES = ("log_cleanup_script", "ai_log_cleanup")

# 各场景 target 必填 key（缺失直接抛 ValueError，由顶层 catch 兜底落库）
REQUIRED_TARGET_KEYS = {
    "process_restart": ("service",),
    "disk_clean": ("mount", "path"),
    "cache_clean": ("mode",),
    "log_cleanup_script": ("mount",),  # category 可选, 缺省跑全部(见 run_cleanup.build_cleanup_command)
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
        # 验证语义: 清理须降到安全水位以下(触发=必要性, 验证=是否真恢复)
        return pct < config.DISK_LOW_PCT
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
        logger.exception("自愈流程异常: scene=%s server_id=%s", scene, server_id)
        action_id = None
        try:
            record = _persist(server_id, scene, target, "high",
                              action_name or SCENE_ACTION.get(scene) or "unknown",
                              "failed", triggered_by,
                              execution={"error": str(exc)}, rendered_command=None)
            action_id = record.id
        except Exception:  # noqa: BLE001
            logger.exception("自愈异常落库失败: scene=%s server_id=%s", scene, server_id)
        return {"severity": "high", "status": "failed", "success": False,
                "action_id": action_id,
                "reason": f"自愈流程异常: {exc}"}


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
        probe_out = (probe.get("stdout") or "").strip().lower()
        # process_restart: systemctl is-active 对 inactive/failed 单元返回 exit≠0,
        # 这是"服务异常"信号而非探测失败 → 继续自愈(重启); unknown 表示单元不存在 → 明确拒绝
        if scene == "process_restart" and probe_out in (
                "inactive", "failed", "deactivating", "activating"):
            pass
        else:
            if scene == "process_restart" and probe_out == "unknown":
                hint = (f"systemctl 未找到服务 {target.get('service')}（输出 unknown）: "
                        "可能未安装或非 systemd 管理")
            else:
                hint = next((str(p).strip() for p in
                             (probe.get("error"), probe.get("stderr"),
                              probe.get("stdout")) if p), "未知原因")
            # 探测失败: 完整记录 exit_code/stderr/stdout, 供问题可查(避免仅 "探测失败: None")
            execution = {"error": probe.get("error"),
                         "exit_code": probe.get("exit_code"),
                         "stderr": probe.get("stderr"),
                         "stdout": probe.get("stdout")}
            record = _persist(server_id, scene, target, "low", action_name, "failed",
                              triggered_by, execution=execution,
                              rendered_command=None)
            suffix = ""
            if probe.get("exit_code") is not None and "exit_code=" not in hint:
                suffix = f" [exit_code={probe['exit_code']}]"
            return {"severity": "low", "status": "failed", "success": False,
                    "action_id": record.id, "action_name": action_name,
                    "reason": f"探测失败: {hint}{suffix}"}

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

    # ---- 2. 冷却去重(同操作短时间内已执行过, 防审批疲劳) ----
    if approval.cooldown_active(server_id, scene, action_name):
        return {"severity": "ok", "status": "noop", "success": True,
                "reason": f"{action_name} 处于冷却期内, 跳过重复处理"}

    # ---- 3. 影响面采集(仅脚本类: dry-run 只读预览, 供审批决策依据) ----
    impact = None
    if action_name == "run_cleanup_script":
        try:
            dry_cmd = run_cleanup.build_dry_run_command(target.get("category", ""))
        except ValueError as exc:
            # 白名单外 category → 拒绝(与 hard_rule 同语义), 不逃逸为顶层 failed
            record = _persist(server_id, scene, target, "high", action_name,
                              "rejected", triggered_by, reasons=[str(exc)],
                              rendered_command=None)
            return {"severity": "high", "status": "rejected", "success": False,
                    "action_id": record.id, "action_name": action_name,
                    "reasons": [str(exc)],
                    "reason": "审批出口拒绝: " + str(exc)}
        dry = actions.exec_ssh(server_id, dry_cmd, timeout=60)
        if dry.get("success"):
            impact = run_cleanup.parse_dry_run_output(dry.get("stdout", ""))
        else:
            # 影响面未知(采集失败) → 留空交由 decide fail-closed 到 approval
            logger.warning("影响面采集失败: server_id=%s action=%s err=%s",
                           server_id, action_name, dry.get("error"))
    elif target.get("path"):
        # 单文件动作: 影响面=1 个文件(大小未知, 交由 AI 结合扫描/常识判断)
        impact = {"files": 1}

    # ---- 4. 统一审批出口(触发与审批解耦: 操作影响风险 → auto/approval/reject) ----
    d = approval.decide(
        server_id, action_name, target,
        {"scene": scene, "triggered_by": triggered_by,
         "metric": metric, "impact": impact},
    )
    if d["decision"] == "reject":
        record = _persist(server_id, scene, target, d["severity"], action_name,
                          "rejected", triggered_by, reasons=d["reasons"],
                          rendered_command=None)
        return {"severity": d["severity"], "status": "rejected",
                "success": False, "action_id": record.id,
                "action_name": action_name, "reasons": d["reasons"],
                "reason": "审批出口拒绝: " + "; ".join(d["reasons"])}
    if d["decision"] == "approval":
        record = _persist(server_id, scene, target, d["severity"], action_name,
                          "pending", triggered_by, reasons=d["reasons"],
                          rendered_command=None)
        return {"severity": d["severity"], "status": "pending", "success": False,
                "action_id": record.id, "reasons": d["reasons"],
                "action_name": action_name,
                "message": "需人工审批, 已生成审批单"}
    if d["decision"] != "auto":
        # fail-open 防护: 未知决策值一律 fail-closed 挂审批单, 绝不直接执行
        record = _persist(server_id, scene, target, "high", action_name,
                          "pending", triggered_by, reasons=d["reasons"],
                          rendered_command=None)
        return {"severity": "high", "status": "pending", "success": False,
                "action_id": record.id, "reasons": d["reasons"],
                "action_name": action_name,
                "message": "审批出口异常决策, 已按人工审批处理"}

    # ---- 5. auto: 渲染(模板白名单, 全流程仅渲染一次) + 执行验证 ----
    try:
        command = actions.render_command(action_name, target)
    except ValueError as exc:
        # hard_rule 已在 decide 内完成白名单校验, 此处仅兜底渲染-执行间的配置竞态
        _persist(server_id, scene, target, d["severity"], action_name,
                 "failed", triggered_by, reasons=[str(exc)],
                 rendered_command=None)
        return {"severity": d["severity"], "status": "failed", "success": False,
                "reason": str(exc)}

    record = _persist(server_id, scene, target, d["severity"], action_name,
                      "executing", triggered_by, reasons=d["reasons"],
                      rendered_command=command)
    record = execute_and_verify(record, target, command)

    result = {
        "severity": d["severity"], "status": record.status,
        "success": record.success, "action_id": record.id,
        "action_name": action_name, "reasons": d["reasons"],
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
      0. 清理类场景记录执行前磁盘基线(仅供审计参考, 不再参与验证判定);
      1. 经 exec_action 执行写命令(支持脚本 stdin 注入),失败置 failed;
      2. 用 detect.probe_command 探测 + verify_recovered 判定(清理类须降到
         DISK_LOW_PCT 安全水位以下) → verified(success=True)/verification_failed(success=False)。
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
