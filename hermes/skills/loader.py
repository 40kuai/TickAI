"""Skill loader — reads/writes skill .md files in hermes/skills/library/.

A skill is a Markdown file with optional YAML frontmatter:

    ---
    name: detect_oom_killed
    description: Find pods killed by OOMKiller
    trigger: scheduled_daily
    severity: warning
    ---

    # Body — instructions for the LLM

The frontmatter is metadata; the body is what gets used as the LLM's
analysis prompt when the skill is run.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any

import yaml

# Compute paths relative to this file
from pathlib import Path as _Path
from hermes.data.db import session_scope
from hermes.data.models import SkillVersion


# Default skills directory — relative to the project root
_THIS = _Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent.parent  # /Users/.../Documents/ai
SKILLS_DIR = _PROJECT_ROOT / "hermes" / "skills" / "library"


class SkillLoadError(ValueError):
    """Raised when a skill cannot be loaded or saved."""


# ============================================================
# Parsing
# ============================================================

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(?P<meta>.*?)\n---\s*\n?(?P<body>.*)",
    re.DOTALL,
)


def _parse_skill(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse a skill .md content into (frontmatter_dict, body_string)."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    try:
        meta = yaml.safe_load(m.group("meta")) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillLoadError("frontmatter must be a YAML mapping")
    return meta, m.group("body").strip()


def _filename_to_name(path: Path) -> str:
    """Convert a filename like 'detect_oom.md' to skill name 'detect_oom'."""
    return path.stem


# ============================================================
# Listing
# ============================================================

def list_skills(skills_dir: str | Path = SKILLS_DIR) -> List[Dict[str, Any]]:
    """List all skills in the directory.

    Returns a list of dicts with keys: name, description, trigger, severity,
    body (truncated to first 200 chars for UI), path.
    """
    p = Path(skills_dir)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(p.glob("*.md")):
        try:
            skill = _load_one(f)
        except Exception as exc:  # noqa: BLE001
            out.append({
                "name": _filename_to_name(f),
                "description": f"[parse error: {exc}]",
                "trigger": "",
                "severity": "",
                "body": "",
                "path": str(f),
                "error": str(exc),
            })
            continue
        out.append(skill)
    return out


def _load_one(path: Path) -> Dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    meta, body = _parse_skill(content)
    name = meta.get("name") or _filename_to_name(path)
    return {
        "name": name,
        "description": str(meta.get("description", "")),
        "trigger": str(meta.get("trigger", "")),
        "severity": str(meta.get("severity", "")),
        "body": body,
        "path": str(path),
    }


# ============================================================
# Loading
# ============================================================

def load_skill(name: str, skills_dir: str | Path = SKILLS_DIR) -> Dict[str, Any]:
    """Load a specific skill by name.

    Lookup by frontmatter `name` field, falling back to filename.
    Raises SkillLoadError if not found.
    """
    p = Path(skills_dir)
    if not p.exists():
        raise SkillLoadError(f"skills directory does not exist: {p}")

    for f in p.glob("*.md"):
        try:
            meta, body = _parse_skill(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        fname_name = _filename_to_name(f)
        if meta.get("name") == name or fname_name == name:
            return {
                "name": meta.get("name") or fname_name,
                "description": str(meta.get("description", "")),
                "trigger": str(meta.get("trigger", "")),
                "severity": str(meta.get("severity", "")),
                "body": body,
                "path": str(f),
            }
    raise SkillLoadError(f"skill not found: {name!r} (in {p})")


# ============================================================
# Saving
# ============================================================

def save_skill(
    name: str,
    content: str,
    skills_dir: str | Path = SKILLS_DIR,
    reason: str = "manual",
) -> str:
    """Save a skill to disk and create a SkillVersion entry.

    Returns the file path written. The filename is derived from the
    frontmatter `name` field (or the provided `name` as fallback).
    """
    p = Path(skills_dir)
    p.mkdir(parents=True, exist_ok=True)

    # Filename = sanitized name
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    path = p / f"{safe_name}.md"

    # Compute diff vs previous content
    diff = ""
    if path.exists():
        try:
            import difflib
            old = path.read_text(encoding="utf-8").splitlines(keepends=True)
            new = content.splitlines(keepends=True)
            diff_lines = difflib.unified_diff(old, new, fromfile="before", tofile="after", n=2)
            diff = "".join(diff_lines)
        except Exception:
            diff = "[diff unavailable]"

    path.write_text(content, encoding="utf-8")

    # Record version
    _record_skill_version(name, content, diff, reason)

    return str(path)


def _record_skill_version(name: str, content: str, diff: str, reason: str) -> None:
    """Persist a SkillVersion entry for the just-saved skill."""
    try:
        with session_scope() as s:
            # Determine next version number
            from sqlalchemy import select, func
            max_v = s.execute(
                select(func.max(SkillVersion.version))
                .where(SkillVersion.skill_name == name)
            ).scalar() or 0
            s.add(SkillVersion(
                skill_name=name,
                version=int(max_v) + 1,
                content=content,
                diff=diff,
                reason=reason,
                created_at=datetime.utcnow(),
            ))
    except Exception:
        # Don't block saves on DB errors
        pass
