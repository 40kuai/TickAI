"""AI 清理策略校验与执行决策(通道二).

AI 只读扫描(scan_log_cleanup)后由 LLM 产出清理策略 JSON, 本模块:
- 逐项映射到 actions 模板白名单(校验失败拒绝并记录原因, 绝不静默放行)
- 合法项统一经 approval.decide 三态决策: reject 拒绝 / approval 挂审批单
  (plan_id 归组, 人工 approve 后经 execute_and_verify 执行) / auto 直接执行+验证。
- AI 绝不绕过统一审批出口直接执行写操作。
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from hermes.data import db
from hermes.data.models import SelfHealAction

from . import actions, approval, orchestrator

logger = logging.getLogger(__name__)

# AI 策略项类型 → action_name(映射到 actions 模板白名单)
_TYPE_ACTIONS = {
    "truncate_file": "truncate_log",
    "journal_vacuum": "journal_vacuum",
    "docker_log_truncate": "docker_log_truncate",
    "run_cleanup_category": "run_cleanup_script",
}

# mount 白名单: 必须是以 / 开头的安全绝对路径(仅字母数字 / . _ -), 防注入
_MOUNT_RE = re.compile(r"^/[A-Za-z0-9/._-]*$")


def _validate_item(item: Dict[str, Any], mount: str = "/") -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """校验单个 AI 清理项。返回 (ok, {action,target,severity,reasons}, reason/None)。"""
    if not isinstance(item, dict):
        return False, None, "非法清理项: 必须是对象"
    itype = (item or {}).get("type", "")
    if itype not in _TYPE_ACTIONS:
        return False, None, f"未知类型: {itype!r}"
    action_name = _TYPE_ACTIONS[itype]

    if itype == "truncate_file":
        path = str(item.get("path", ""))
        target = {"mount": mount, "path": path}
    elif itype == "docker_log_truncate":
        path = str(item.get("path", ""))
        target = {"mount": mount, "path": path}
    elif itype == "journal_vacuum":
        target = {"mount": mount, "size": str(item.get("size", ""))}
    elif itype == "run_cleanup_category":
        target = {"mount": mount, "category": str(item.get("category", ""))}
    else:  # 防御: _TYPE_ACTIONS 新增类型但未加分支时绝不静默
        raise AssertionError(f"_TYPE_ACTIONS 未实现分支: {itype!r}")

    # 模板 + 白名单校验(失败抛 ValueError, 绝不 fallback)
    try:
        actions.render_command(action_name, target)
    except ValueError as exc:
        return False, None, f"{itype} 校验失败(白名单拒绝): {exc}"

    reasons = [f"AI 建议清理: {itype}"]
    return True, {"action": action_name, "target": target,
                  "severity": "high", "reasons": reasons}, None


def create_plan(server_id: int, strategy: Dict[str, Any],
                plan_id: Optional[str] = None) -> Dict[str, Any]:
    """校验 AI 策略 JSON 并生成 pending 审批单(plan_id 归组)。

    strategy: {"mount": "/", "items": [{"type": ..., ...}, ...]}
    返回 {plan_id, accepted, rejected, items:[{ok, action/target or reason}]}
    """
    import uuid
    plan_id = plan_id or f"plan-{uuid.uuid4().hex[:12]}"
    mount = str((strategy or {}).get("mount") or "/")

    # mount 严格白名单校验(仅安全绝对路径), 防探测命令注入
    if not _MOUNT_RE.match(mount):
        return {"plan_id": plan_id, "accepted": 0, "rejected": 1, "auto": 0,
                "items": [{"ok": False, "reason": f"mount 非法: {mount!r}"}]}

    items = (strategy or {}).get("items", [])
    if not isinstance(items, list):
        return {"plan_id": plan_id, "accepted": 0, "rejected": 1, "auto": 0,
                "items": [{"ok": False, "reason": "items 必须是数组"}]}

    accepted = 0
    rejected = 0
    auto = 0
    out_items: List[Dict[str, Any]] = []
    for item in items:
        ok, detail, reason = _validate_item(item, mount)
        if not ok:
            rejected += 1
            out_items.append({"ok": False, "reason": reason})
            continue
        # 影响面: AI 策略项为单目标(文件/日志), size_mb 取策略估量(非法则该项拒绝)
        raw_size = item.get("size_mb")
        try:
            size_mb = float(raw_size) if raw_size is not None else None
            if size_mb is not None and (isinstance(raw_size, bool) or not math.isfinite(size_mb)):
                raise ValueError("not finite")
        except (TypeError, ValueError):
            rejected += 1
            out_items.append({"ok": False, "reason": f"非法 size_mb: {raw_size!r}"})
            continue
        impact = {"files": 1, "size_mb": size_mb}
        # 统一审批出口: 操作影响风险 → reject 拒绝 / approval 挂单 / auto 直接执行
        d = approval.decide(
            server_id, detail["action"], detail["target"],
            {"scene": "ai_log_cleanup", "triggered_by": "ai",
             "impact": impact, "metric": None},
        )
        if d["decision"] == "reject":
            rejected += 1
            out_items.append({"ok": False,
                              "reason": "审批出口拒绝: " + "; ".join(d["reasons"])})
            continue
        if d["decision"] == "approval":
            _persist_pending(server_id, plan_id, detail, reasons=d["reasons"])
            accepted += 1
            out_items.append({"ok": True, "action": detail["action"],
                              "target": detail["target"], "status": "pending"})
            continue
        if d["decision"] != "auto":
            # fail-open 防护: 未知决策值一律 fail-closed 挂审批单, 绝不直接执行
            _persist_pending(server_id, plan_id, detail, reasons=d["reasons"])
            accepted += 1
            out_items.append({"ok": True, "action": detail["action"],
                              "target": detail["target"], "status": "pending"})
            continue
        # auto: 统一出口放行 → 冷却去重(防 AI 周期性重复直执写操作) → 直接执行 + 验证
        if approval.cooldown_active(server_id, "ai_log_cleanup", detail["action"]):
            _persist_pending(server_id, plan_id, detail,
                             reasons=d["reasons"] + ["冷却期内, auto 降级为人工审批"])
            accepted += 1
            out_items.append({"ok": True, "action": detail["action"],
                              "target": detail["target"], "status": "pending"})
            continue
        try:
            record = _persist_and_execute_auto(server_id, plan_id, detail,
                                               d["reasons"], d["severity"])
            record = orchestrator.execute_and_verify(record, detail["target"],
                                                     record.rendered_command)
            auto += 1
            accepted += 1
            out_items.append({"ok": True, "action": detail["action"],
                              "target": detail["target"], "status": record.status})
        except Exception as exc:  # noqa: BLE001
            # 渲染/执行异常兜底: 该项落 failed, 不拖垮整批策略
            logger.exception("AI auto 项执行异常: server_id=%s plan_id=%s action=%s",
                             server_id, plan_id, detail["action"])
            _persist_auto_failed(server_id, plan_id, detail, d["reasons"],
                                 d["severity"], exc)
            auto += 1
            accepted += 1
            out_items.append({"ok": True, "action": detail["action"],
                              "target": detail["target"], "status": "failed"})

    return {"plan_id": plan_id, "accepted": accepted,
            "rejected": rejected, "auto": auto, "items": out_items}


def _persist_pending(server_id: int, plan_id: str, detail: Dict[str, Any],
                     reasons: Optional[List[str]] = None) -> None:
    with db.session_scope() as s:
        row = SelfHealAction(
            server_id=server_id,
            scene="ai_log_cleanup",
            target=json.dumps(detail["target"], ensure_ascii=False),
            severity=detail["severity"],
            action_name=detail["action"],
            status="pending",
            triggered_by="ai",
            plan_id=plan_id,
            grade_reasons=json.dumps(reasons or detail["reasons"], ensure_ascii=False),
        )
        s.add(row)
        s.flush()
        return row


def _persist_and_execute_auto(server_id: int, plan_id: str, detail: Dict[str, Any],
                              reasons: List[str], severity: str) -> Any:
    """auto 决策项: 落库 executing → execute_and_verify(统一出口放行后直接执行)。

    severity 来自 decide 返回值(对 auto 恒为 low), 单一来源。
    """
    command = actions.render_command(detail["action"], detail["target"])
    with db.session_scope() as s:
        row = SelfHealAction(
            server_id=server_id, scene="ai_log_cleanup",
            target=json.dumps(detail["target"], ensure_ascii=False),
            severity=severity, action_name=detail["action"],
            status="executing", triggered_by="ai", plan_id=plan_id,
            rendered_command=command,
            grade_reasons=json.dumps(reasons, ensure_ascii=False),
        )
        s.add(row)
        s.flush()
        return row


def _persist_auto_failed(server_id: int, plan_id: str, detail: Dict[str, Any],
                         reasons: List[str], severity: str, exc: Exception) -> None:
    """auto 项执行异常兜底: 落 failed 审计记录, 不拖垮整批策略。"""
    with db.session_scope() as s:
        row = SelfHealAction(
            server_id=server_id, scene="ai_log_cleanup",
            target=json.dumps(detail["target"], ensure_ascii=False),
            severity=severity, action_name=detail["action"],
            status="failed", triggered_by="ai", plan_id=plan_id,
            execution_result=json.dumps({"error": str(exc)}, ensure_ascii=False),
            grade_reasons=json.dumps(reasons, ensure_ascii=False),
        )
        s.add(row)
