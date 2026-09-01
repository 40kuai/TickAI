"""自愈闭环 API 路由（KR2 Task 7）。

提供 /api/selfheal 下的:
- GET  /scenes                       3 场景元数据
- POST /run                          触发一次自愈闭环(调 orchestrator.run_selfheal)
- GET  /actions                      动作列表(status_filter + created_at 倒序)
- POST /actions/{id}/approve         审批并执行:pending→重渲染→执行→验证
- POST /actions/{id}/reject          驳回
- GET  /stats                        成功率统计

安全模型:
- 所有路由都要求登录 (user=Depends(get_current_user), api.deps,HttpOnly Cookie)
- 写命令只来自 actions.render_command 模板白名单(见 hermes.selfheal.actions)
- approve 时对 pending 记录重渲染命令(其 rendered_command 为 None),渲染失败转 failed
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from hermes.data import db
from hermes.data.models import SelfHealAction, User
from hermes.selfheal import actions, orchestrator

from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/selfheal", tags=["selfheal"])

# 状态机里 "已执行(参与成功率分母)" 的状态集合
_EXECUTED_STATUSES = ("verified", "verification_failed", "failed", "executed")


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class SelfHealRunRequest(BaseModel):
    server_id: int
    scene: str
    target: Dict[str, Any]
    action_name: Optional[str] = None


# ---------------------------------------------------------------------------
# 场景元数据
# ---------------------------------------------------------------------------
def _scene_metadata() -> List[Dict[str, Any]]:
    """从 orchestrator.SCENE_ACTION 迭代派生,避免硬编码场景名与动作映射分叉。"""
    return [
        {"name": scene,
         "action_name": action_name,
         "required_target": list(orchestrator.REQUIRED_TARGET_KEYS.get(scene, ()))}
        for scene, action_name in orchestrator.SCENE_ACTION.items()
    ]


@router.get("/scenes")
def list_scenes(user: User = Depends(get_current_user)):
    """返回 3 个自愈场景的元数据。"""
    return _scene_metadata()


# ---------------------------------------------------------------------------
# 触发自愈
# ---------------------------------------------------------------------------
@router.post("/run")
def run_selfheal(req: SelfHealRunRequest, user: User = Depends(get_current_user)):
    """触发一次自愈闭环。任何异常收敛为 400(异常消息原样返回给调用方)。"""
    try:
        return orchestrator.run_selfheal(
            server_id=req.server_id,
            scene=req.scene,
            target=req.target,
            triggered_by="user",
            action_name=req.action_name,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# 动作列表
# ---------------------------------------------------------------------------
@router.get("/actions")
def list_actions(
    status_filter: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
):
    """按 created_at 倒序返回动作列表,最多 100 条,支持 status 过滤。"""
    with db.session_scope() as s:
        q = s.query(SelfHealAction)
        if status_filter:
            q = q.filter(SelfHealAction.status == status_filter)
        rows = q.order_by(SelfHealAction.created_at.desc()).limit(100).all()
        return [row.to_dict() for row in rows]


# ---------------------------------------------------------------------------
# 审批 / 驳回
# ---------------------------------------------------------------------------
def _get_action_or_404(action_id: int) -> SelfHealAction:
    with db.session_scope() as s:
        row = s.get(SelfHealAction, action_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Self-heal action {action_id} not found",
            )
        # 触发懒加载 server 关系,避免 session 关闭后 DetachedInstanceError
        _ = row.server
        return row


def _to_dict(action_id: int) -> Dict[str, Any]:
    """在打开的 session 内返回 to_dict(需懒加载 server_name)。"""
    with db.session_scope() as s:
        return s.get(SelfHealAction, action_id).to_dict()


def _fail_action(action_id: int, reason: str, approver: Optional[str] = None) -> Dict[str, Any]:
    """将动作置为 failed 并落库(渲染/执行/验证异常的统一兜底)。

    approver: 审批路径的审批人用户名,渲染失败等未进入执行器时补记。
    已有 execution_result(如验证异常时已落真实执行结果)不会被覆盖。
    """
    now = datetime.now(timezone.utc)
    with db.session_scope() as s:
        record = s.get(SelfHealAction, action_id)
        record.status = "failed"
        record.success = False
        if not record.execution_result:
            record.execution_result = json.dumps({"error": reason}, ensure_ascii=False)
        if approver:
            record.approver = approver
            record.approved_at = now
        record.executed_at = now
    return _to_dict(action_id)


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int, user: User = Depends(get_current_user)):
    """审批高危动作: 重渲染命令(pending 时 rendered_command 为 None)→执行→验证。

    执行/验证/落库状态机统一委托 orchestrator.execute_and_verify(与低危自主执行共用),
    避免双源维护状态机与审计字段。
    状态流转:
      pending → executed → verified / verification_failed
      渲染/执行/验证任何异常 → failed
    """
    row = _get_action_or_404(action_id)
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"action {action_id} is not pending (status={row.status})",
        )

    # ---- 1. 从 target JSON 反序列化并重渲染命令(模板白名单校验) ----
    try:
        target = json.loads(row.target or "{}")
    except (ValueError, TypeError):
        return _fail_action(action_id, f"invalid target json: {row.target!r}",
                            approver=user.username)
    try:
        command = actions.render_command(row.action_name, target)
    except ValueError as exc:
        return _fail_action(action_id, f"渲染命令失败: {exc}",
                            approver=user.username)

    # ---- 2. 执行 + 验证 + 落库(状态机与审计字段统一在 orchestrator 维护) ----
    try:
        orchestrator.execute_and_verify(row, target, command, approver=user.username)
    except Exception as exc:  # noqa: BLE001
        return _fail_action(action_id, f"执行异常: {exc}")
    return _to_dict(action_id)


@router.post("/actions/{action_id}/reject")
def reject_action(action_id: int, user: User = Depends(get_current_user)):
    """驳回高危动作,仅置 rejected,不执行任何写命令。"""
    row = _get_action_or_404(action_id)
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"action {action_id} is not pending (status={row.status})",
        )
    with db.session_scope() as s:
        record = s.get(SelfHealAction, action_id)
        record.status = "rejected"
        record.approver = user.username
        record.approved_at = datetime.now(timezone.utc)
    return _to_dict(action_id)


# ---------------------------------------------------------------------------
# 成功率统计
# ---------------------------------------------------------------------------
@router.get("/stats")
def get_stats(user: User = Depends(get_current_user)):
    """成功率统计。

    total_executed = verified / verification_failed / failed / executed 计数
    success        = success=True 计数
    success_rate   = success / total_executed * 100,保留 1 位小数
    pending        = status=pending 计数
    """
    with db.session_scope() as s:
        query = s.query(SelfHealAction)
        total = query.filter(SelfHealAction.status.in_(_EXECUTED_STATUSES)).count()
        success = query.filter(SelfHealAction.success.is_(True)).count()
        pending = query.filter(SelfHealAction.status == "pending").count()
    success_rate = round(success / total * 100, 1) if total else 0.0
    return {
        "total_executed": total,
        "success": success,
        "success_rate": success_rate,
        "pending": pending,
    }
