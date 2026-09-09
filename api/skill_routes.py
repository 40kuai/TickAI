"""Skill routes - list skills from hermes/skills/library (read-only).

Exposes skill metadata (name / description / trigger / severity / path) to
the frontend Skills page. Pure read-only: no create/update/delete, and the
skill body is not returned to keep the payload small.

P1 能力治理增量(反馈闭环): 技能执行反馈(SkillOutcome)查询/标注 + 进化历史(SkillVersion)。
反馈数据是 SkillEvolver 的学习信号, 标注入口先于进化门禁落地。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from hermes.data import db, models
from hermes.skills.loader import list_skills, load_skill

from .deps import get_current_user

router = APIRouter(prefix="/api/skills", tags=["skills"])

_VALID_DECISIONS = ("accepted", "rejected")


class FeedbackRequest(BaseModel):
    decision: str
    notes: str = ""


@router.get("")
def list_all_skills(user=Depends(get_current_user)):
    """List all available skills (metadata only, no body)."""
    skills = []
    for s in list_skills():
        skills.append({
            "name": s.get("name"),
            "description": s.get("description"),
            "trigger": s.get("trigger"),
            "severity": s.get("severity"),
            "path": s.get("path"),
        })
    return {"skills": skills, "count": len(skills)}


@router.get("/{name}")
def get_skill_detail(name: str, user=Depends(get_current_user)):
    """Get a single skill's full content (frontmatter + body)."""
    try:
        return load_skill(name)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{name}' not found",
        ) from exc


@router.get("/{name}/feedback")
def get_skill_feedback(name: str, user=Depends(get_current_user)):
    """List execution feedback (SkillOutcome) for a skill, newest first."""
    with db.session_scope() as s:
        rows = (
            s.query(models.SkillOutcome)
            .filter_by(skill_name=name)
            .order_by(models.SkillOutcome.run_at.desc())
            .all()
        )
        return {
            "skill_name": name,
            "feedback": [
                {
                    "id": r.id,
                    "skill_version": r.skill_version,
                    "run_at": r.run_at.isoformat() if r.run_at else None,
                    "findings_summary": r.findings_summary,
                    "user_decision": r.user_decision,
                    "decision_notes": r.decision_notes,
                    "outcome_effect": r.outcome_effect,
                }
                for r in rows
            ],
            "count": len(rows),
        }


@router.post("/{name}/feedback/{outcome_id}")
def mark_skill_feedback(
    name: str, outcome_id: int, req: FeedbackRequest,
    user=Depends(get_current_user),
):
    """Mark one execution outcome as accepted/rejected (进化反馈标注).

    decision 仅接受 accepted|rejected; 允许覆盖重新标注(以最新为准)。
    """
    if req.decision not in _VALID_DECISIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"decision 必须是 {', '.join(_VALID_DECISIONS)}",
        )
    with db.session_scope() as s:
        row = (
            s.query(models.SkillOutcome)
            .filter_by(id=outcome_id, skill_name=name)
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"反馈记录 {outcome_id} 不存在或不属于技能 '{name}'",
            )
        row.user_decision = req.decision
        row.decision_at = datetime.now()
        row.decision_notes = req.notes
    return {"id": outcome_id, "decision": req.decision}


@router.get("/{name}/versions")
def get_skill_versions(name: str, user=Depends(get_current_user)):
    """List evolution history (SkillVersion), newest first.

    diff 截断返回(保持 payload 小); 完整内容走 get_skill_detail。
    """
    with db.session_scope() as s:
        rows = (
            s.query(models.SkillVersion)
            .filter_by(skill_name=name)
            .order_by(models.SkillVersion.version.desc())
            .all()
        )
        return {
            "skill_name": name,
            "versions": [
                {
                    "version": r.version,
                    "reason": r.reason,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "diff": (r.diff or "")[:500],
                }
                for r in rows
            ],
            "count": len(rows),
        }
