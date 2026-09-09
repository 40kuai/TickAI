"""Skill routes - list skills from hermes/skills/library (read-only).

Exposes skill metadata (name / description / trigger / severity / path) to
the frontend Skills page. Pure read-only: no create/update/delete, and the
skill body is not returned to keep the payload small.

P1 能力治理增量(反馈闭环): 技能执行反馈(SkillOutcome)查询/标注 + 进化历史(SkillVersion)。
反馈数据是 SkillEvolver 的学习信号, 标注入口先于进化门禁落地。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    P2 门禁: 每条带 status(active/pending/rolled_back), pending=候选待审批。
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
                    "id": r.id,
                    "version": r.version,
                    "reason": r.reason,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "diff": (r.diff or "")[:500],
                }
                for r in rows
            ],
            "count": len(rows),
        }


# ============================================================
# P2 技能进化门禁: 提议进化(生成候选待审) → 批准生效/拒绝/回滚
# ============================================================


@router.post("/{name}/evolve")
def evolve_skill(
    name: str,
    max_tokens: Optional[int] = Query(None, ge=256, le=16384),
    user=Depends(get_current_user),
):
    """生成进化候选版本(pending), 不写盘; 人工审批后才生效(fail-closed 门禁).

    max_tokens: 限制 LLM 输出长度, 防止长技能生成超时(默认不限制, 由后端兜底)。
    """
    import difflib
    from pathlib import Path

    from hermes.agents.skill_evolver import EvolutionError, evolve_skill as _evolve

    try:
        new_content = _evolve(name, save=False, max_tokens=max_tokens)
    except EvolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"进化生成失败: {exc}",
        ) from exc

    # fail-closed: LLM 输出必须为完整技能(有效 frontmatter + 正文), 残片直接拒绝落候选
    from hermes.skills.loader import validate_skill_content
    invalid = validate_skill_content(new_content, name)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"进化生成失败: 生成内容校验未通过 - {invalid}",
        )

    # diff vs 当前线上 .md 全文
    diff = ""
    try:
        cur = load_skill(name)
        old_text = Path(cur["path"]).read_text(encoding="utf-8")
        diff = "".join(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="before", tofile="after", n=2,
        ))[:2000]
    except Exception:
        diff = "[diff unavailable]"

    with db.session_scope() as s:
        from sqlalchemy import func, select
        max_v = s.execute(
            select(func.max(models.SkillVersion.version))
            .where(models.SkillVersion.skill_name == name)
        ).scalar() or 0
        rec = models.SkillVersion(
            skill_name=name,
            version=int(max_v) + 1,
            content=new_content,
            diff=diff,
            reason="auto_evolve",
            status="pending",
        )
        s.add(rec)
        s.flush()
        vid = rec.id
        ver = rec.version
    return {"version_id": vid, "version": ver, "status": "pending", "diff": diff}


@router.post("/{name}/versions/{vid}/approve")
def approve_skill_version(name: str, vid: int, user=Depends(get_current_user)):
    """批准候选版本: 写入线上 .md 生效, 候选标记已处置. 仅 pending 可批准."""
    from hermes.skills.loader import save_skill

    with db.session_scope() as s:
        row = (
            s.query(models.SkillVersion)
            .filter_by(id=vid, skill_name=name)
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"版本 {vid} 不存在或不属于技能 '{name}'",
            )
        if row.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"版本 {vid} 状态为 {row.status}, 仅 pending 候选可批准",
            )
        content = row.content
    # fail-closed: 候选内容必须为完整技能(有效 frontmatter + 正文), 坏候选拒绝写盘
    from hermes.skills.loader import validate_skill_content
    invalid = validate_skill_content(content, name)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"候选版本 {vid} 内容校验未通过, 拒绝批准 - {invalid}",
        )
    # 写盘生效: 独立事务, 避免嵌套 session(SQLite 单连接锁)
    # record_version=False: 候选记录本身流转为 active(线上), 不产生双记录/版本跳号
    save_skill(name, content, reason="auto_evolve", record_version=False)
    with db.session_scope() as s:
        row = s.query(models.SkillVersion).filter_by(id=vid, skill_name=name).first()
        row.status = "active"  # 候选已批准并成为线上版本; 回滚才用 rolled_back
    return {"status": "approved", "version_id": vid}


@router.post("/{name}/versions/{vid}/reject")
def reject_skill_version(name: str, vid: int, user=Depends(get_current_user)):
    """拒绝候选版本: 不写盘, 标记 rejected. 仅 pending 可拒绝."""
    with db.session_scope() as s:
        row = (
            s.query(models.SkillVersion)
            .filter_by(id=vid, skill_name=name)
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"版本 {vid} 不存在或不属于技能 '{name}'",
            )
        if row.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"版本 {vid} 状态为 {row.status}, 仅 pending 候选可拒绝",
            )
        row.status = "rejected"
    return {"status": "rejected", "version_id": vid}


@router.post("/{name}/versions/{vid}/rollback")
def rollback_skill_version(name: str, vid: int, user=Depends(get_current_user)):
    """回滚到历史版本: 以该版本内容生成新的 active 版本(不破坏既有历史)."""
    from hermes.skills.loader import save_skill

    with db.session_scope() as s:
        row = (
            s.query(models.SkillVersion)
            .filter_by(id=vid, skill_name=name)
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"版本 {vid} 不存在或不属于技能 '{name}'",
            )
        content = row.content
        target = row.version
        # 防无效回滚: 目标内容与当前线上(最新 active 版本)一致时拒绝, 避免产生冗余版本
        cur = (
            s.query(models.SkillVersion)
            .filter_by(skill_name=name, status="active")
            .order_by(models.SkillVersion.version.desc())
            .first()
        )
        if cur is not None and cur.content == content:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"版本 {vid} 的内容与当前线上一致, 无需回滚",
            )
    # 独立事务写盘, 避免嵌套 session
    save_skill(name, content, reason="rollback")
    return {"status": "rolled_back", "version_id": vid, "target_version": target}
