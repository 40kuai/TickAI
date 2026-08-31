"""Skill routes - list skills from hermes/skills/library (read-only).

Exposes skill metadata (name / description / trigger / severity / path) to
the frontend Skills page. Pure read-only: no create/update/delete, and the
skill body is not returned to keep the payload small.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from hermes.skills.loader import list_skills, load_skill

from .deps import get_current_user

router = APIRouter(prefix="/api/skills", tags=["skills"])


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
