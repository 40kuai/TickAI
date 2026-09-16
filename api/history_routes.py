"""History routes - unified operation history (runs + skills + conversations).

历史记录页统一查询入口:把三类操作记录合并展示,便于操作审核。
  1. run_history(服务器操作,如 SSH 磁盘/资源/服务检查)
  2. skill_outcomes(skill 巡检,如 diagnose_prometheus_anomaly 的 nfc 健康巡检)
  3. llm_conversations(AI 对话)

每类记录统一为 `{type, id, time, title, subtitle, status, detail}` 结构,
前端按 type 渲染不同标签与详情。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from hermes.data.db import session_scope
from hermes.data.models import RunRecord, Server, SkillOutcome, Conversation

from .deps import get_current_user

router = APIRouter(prefix="/api/history", tags=["history"])

# 允许的筛选类型
VALID_TYPES = ("run", "skill", "conversation")


def _status_of(run: RunRecord) -> str:
    return run.status or "unknown"


def _run_to_item(run: RunRecord, server: Optional[Server]) -> dict:
    """RunRecord → 统一历史条目。"""
    return {
        "type": "run",
        "id": run.id,
        "time": run.started_at.isoformat() if run.started_at else None,
        "title": run.command or "-",
        "subtitle": server.name if server else "-",
        "status": _status_of(run),
        "triggered_by": run.triggered_by,
        "duration_ms": run.duration_ms,
        "detail": {
            "server_id": run.server_id,
            "server_name": server.name if server else None,
            "server_host": server.host if server else None,
            "exit_code": run.exit_code,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stdout": run.stdout,
            "stderr": run.stderr,
            "result": _safe_json(run.structured_result),
            "triggered_context": _safe_json(run.triggered_context),
        },
    }


def _skill_to_item(outcome: SkillOutcome) -> dict:
    """SkillOutcome → 统一历史条目。"""
    return {
        "type": "skill",
        "id": outcome.id,
        "time": outcome.run_at.isoformat() if outcome.run_at else None,
        "title": outcome.skill_name or "-",
        "subtitle": outcome.cluster_context or "",
        "status": "success",
        "triggered_by": outcome.triggered_by,
        "duration_ms": None,
        "detail": {
            "skill_version": outcome.skill_version,
            "cluster_context": outcome.cluster_context,
            "findings": _safe_json(outcome.findings_json),
            "summary": outcome.findings_summary,
            "user_decision": outcome.user_decision,
        },
    }


def _conv_to_item(conv: Conversation) -> dict:
    """Conversation → 统一历史条目。"""
    return {
        "type": "conversation",
        "id": conv.id,
        "time": conv.updated_at.isoformat() if conv.updated_at else None,
        "title": conv.title or "New conversation",
        "subtitle": "",
        "status": "success",
        "triggered_by": "user",
        "duration_ms": None,
        "detail": {
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
            "messages": _safe_json(conv.messages_json),
            "total_runs": conv.total_runs,
        },
    }


def _safe_json(text: Optional[str]):
    """Parse a JSON column safely; return None on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


@router.get("")
def query_history(
    user=Depends(get_current_user),
    type: Optional[str] = Query(None, description="Filter by type: run | skill | conversation"),
    status: Optional[str] = Query(None, description="Filter by status"),
    triggered_by: Optional[str] = Query(None, description="Filter by trigger source"),
    since: Optional[str] = Query(None, description="Time window like '1h', '1d', '7d'"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
):
    """Query unified history: server runs + skill outcomes + conversations."""
    if type and type not in VALID_TYPES:
        raise HTTPException(400, f"type 必须是 {'/'.join(VALID_TYPES)} 之一")

    items: list[dict] = []

    with session_scope() as s:
        # 1. 服务器操作 run_history
        if type is None or type == "run":
            q = s.query(RunRecord)
            if status:
                q = q.filter(RunRecord.status == status)
            if triggered_by:
                q = q.filter(RunRecord.triggered_by == triggered_by)
            for run in q.order_by(RunRecord.started_at.desc()).limit(limit).all():
                sv = s.get(Server, run.server_id) if run.server_id else None
                items.append(_run_to_item(run, sv))

        # 2. skill 巡检 skill_outcomes
        if type is None or type == "skill":
            q = s.query(SkillOutcome)
            if triggered_by:
                q = q.filter(SkillOutcome.triggered_by == triggered_by)
            for outcome in q.order_by(SkillOutcome.run_at.desc()).limit(limit).all():
                items.append(_skill_to_item(outcome))

        # 3. AI 对话 llm_conversations
        if type is None or type == "conversation":
            q = s.query(Conversation)
            for conv in q.order_by(Conversation.updated_at.desc()).limit(limit).all():
                items.append(_conv_to_item(conv))

    # 统一按时间倒序
    items.sort(key=lambda i: i.get("time") or "", reverse=True)
    items = items[:limit]

    return {"count": len(items), "runs": items}


@router.get("/{record_id}")
def get_run_detail(
    record_id: int,
    user=Depends(get_current_user),
    type: Optional[str] = Query(None, description="记录类型: run | skill | conversation"),
):
    """Get full detail of a single record (run / skill / conversation).

    三类记录各自独立自增 id,可能重复,故优先按 type 精确定位;
    未指定 type 时按 run → skill → conversation 顺序回退。
    """
    if type and type not in VALID_TYPES:
        raise HTTPException(400, f"type 必须是 {'/'.join(VALID_TYPES)} 之一")

    with session_scope() as s:
        if type == "skill" or type is None:
            outcome = s.get(SkillOutcome, record_id)
            if outcome is not None:
                return _skill_to_item(outcome)
        if type == "run" or type is None:
            run = s.get(RunRecord, record_id)
            if run is not None:
                sv = s.get(Server, run.server_id) if run.server_id else None
                return _run_to_item(run, sv)
        if type == "conversation" or type is None:
            conv = s.get(Conversation, record_id)
            if conv is not None:
                return _conv_to_item(conv)

        raise HTTPException(404, "记录不存在")
