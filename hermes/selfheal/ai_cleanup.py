"""AI 清理策略校验与审批单生成(通道二).

AI 只读扫描(scan_log_cleanup)后由 LLM 产出清理策略 JSON, 本模块:
- 逐项映射到 actions 模板白名单(校验失败拒绝并记录原因, 绝不静默放行)
- 合法项生成 SelfHealAction(status=pending, triggered_by=ai, plan_id 归组)
- AI 绝不直接执行写操作; 全部审批单待人工 approve 后经 execute_and_verify 执行。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from hermes.data import db
from hermes.data.models import SelfHealAction

from . import actions, config

# AI 策略项类型 → action_name(映射到 actions 模板白名单)
_TYPE_ACTIONS = {
    "truncate_file": "truncate_log",
    "journal_vacuum": "journal_vacuum",
    "docker_log_truncate": "docker_log_truncate",
    "run_cleanup_category": "run_cleanup_script",
}


def _validate_item(item: Dict[str, Any], mount: str = "/") -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """校验单个 AI 清理项。返回 (ok, {action,target,severity,reasons}, reason/None)。"""
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
    else:
        return False, None, f"未知类型: {itype!r}"

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

    items = (strategy or {}).get("items", [])
    if not isinstance(items, list):
        return {"plan_id": plan_id, "accepted": 0, "rejected": 1,
                "items": [{"ok": False, "reason": "items 必须是数组"}]}

    accepted = 0
    rejected = 0
    out_items: List[Dict[str, Any]] = []
    for item in items:
        ok, detail, reason = _validate_item(item, mount)
        if not ok:
            rejected += 1
            out_items.append({"ok": False, "reason": reason})
            continue
        # 生成 pending 审批单
        _persist_pending(server_id, plan_id, detail, item)
        accepted += 1
        out_items.append({"ok": True, "action": detail["action"],
                          "target": detail["target"]})

    return {"plan_id": plan_id, "accepted": accepted,
            "rejected": rejected, "items": out_items}


def _persist_pending(server_id: int, plan_id: str, detail: Dict[str, Any],
                     item: Dict[str, Any]) -> None:
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
            grade_reasons=json.dumps(detail["reasons"], ensure_ascii=False),
        )
        s.add(row)
