"""SkillEvolver — uses the LLM to improve a skill based on user feedback.

The flow:
  1. Load the current skill content
  2. Gather recent SkillOutcome rows for the skill
  3. Build a prompt: "here's the current skill, here's the feedback, please improve it"
  4. LLM produces a new .md content
  5. (Optional) save the new content, creating a SkillVersion

This is the "auto-optimize" feature requested in the original spec. It runs
manually (not yet auto-scheduled) — see sprint 2 for auto-evolution.

Safety: the LLM is asked to preserve the read-only contract and not change
the skill's name. The new content is saved as a new version, never
overwriting the latest without an explicit save=True.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy import select

from hermes.data import db
from hermes.data.models import SkillOutcome
from hermes.agents.base import BaseAgent, AgentContext
from hermes.agents.skill_runner import LANGUAGE_DIRECTIVES, _resolve_language
from hermes.skills.loader import (
    load_skill,
    save_skill,
    SKILLS_DIR,
)


class EvolutionError(RuntimeError):
    """Raised when evolution cannot be performed."""


class SkillEvolver(BaseAgent):
    """Improve a skill based on user feedback (modular agent)."""

    name = "skill_evolver"
    description = "Evolves and improves skills using LLM"

    def __init__(
        self,
        llm_client: Any,
        skills_dir: str | Path = SKILLS_DIR,
        max_outcomes: int = 20,
        language: str = "en",
    ):
        self.llm = llm_client
        self.skills_dir = Path(skills_dir)
        self.max_outcomes = max_outcomes
        self.language = _resolve_language(language)

    def run(self, context: AgentContext) -> AgentContext:
        """BaseAgent interface implementation."""
        skill_name = context.get("skill_name")
        auto_save = context.get("auto_save", False)

        if not skill_name:
            raise ValueError("Context must contain 'skill_name'")

        new_content = self.evolve_skill(skill_name, save=auto_save)

        context.set("evolved_skill_content", new_content)
        context.append_history(self.name, f"skill {skill_name} evolved")
        return context

    def evolve_skill(self, skill_name: str, save: bool = False) -> str:
        """Return the new (improved) skill content. Optionally save it.

        Does NOT save by default — caller decides whether to apply the change.
        """
        # 1. Load current skill
        try:
            skill = load_skill(skill_name, self.skills_dir)
        except Exception as exc:
            raise EvolutionError(f"failed to load skill {skill_name!r}: {exc}") from exc

        # 2. Gather outcome history
        outcomes = self._gather_outcomes(skill_name)

        # 3. Build prompt
        prompt = self._build_prompt(skill, outcomes)

        # 4. Call LLM
        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise EvolutionError(f"LLM call failed: {exc}") from exc

        # 5. Extract content
        msg = response["choices"][0]["message"]
        raw_content = msg.get("content") or ""
        new_content = self._extract_skill_content(raw_content)

        if not new_content.strip():
            raise EvolutionError("LLM returned empty content")

        # 6. Optionally save
        if save:
            save_skill(skill_name, new_content, skills_dir=self.skills_dir, reason="auto_evolve")

        return new_content

    # ============================================================
    # Helpers
    # ============================================================

    def _gather_outcomes(self, skill_name: str) -> List[dict]:
        """Read recent outcomes for a skill from the DB."""
        with db.session_scope() as s:
            rows = s.execute(
                select(SkillOutcome)
                .where(SkillOutcome.skill_name == skill_name)
                .order_by(SkillOutcome.run_at.desc())
                .limit(self.max_outcomes)
            ).scalars().all()
            return [
                {
                    "run_at": r.run_at.isoformat() if r.run_at else None,
                    "user_decision": r.user_decision,
                    "decision_notes": r.decision_notes,
                    "outcome_effect": r.outcome_effect,
                    "findings_summary": r.findings_summary[:300],
                }
                for r in rows
            ]

    def _system_prompt(self) -> str:
        return (
            "You are an expert at improving operational analysis skills for Kubernetes clusters.\n\n"
            "A skill is a Markdown file with YAML frontmatter (name, description, trigger, severity) "
            "and a body of instructions for an LLM to follow.\n\n"
            "Your job: given the current skill and recent user feedback, produce an IMPROVED version "
            "that addresses the feedback while keeping the same name and read-only safety guarantees.\n\n"
            "Output format: return ONLY the complete new skill content (frontmatter + body). "
            "Do not include explanations, code fences, or any other text.\n\n"
            f"{LANGUAGE_DIRECTIVES[self.language]}"
        )

    def _build_prompt(self, skill: dict, outcomes: List[dict]) -> str:
        parts = [
            f"## Current skill: {skill['name']}\n",
            "```markdown",
            self._reconstruct_skill(skill),
            "```\n",
        ]

        if outcomes:
            accepted = [o for o in outcomes if o["user_decision"] == "accepted"]
            rejected = [o for o in outcomes if o["user_decision"] == "rejected"]
            parts.append(f"## Recent feedback ({len(outcomes)} outcomes, {len(accepted)} accepted, {len(rejected)} rejected)\n")
            for i, o in enumerate(outcomes, 1):
                parts.append(
                    f"### Outcome #{i} — {o['user_decision']}\n"
                    f"Findings: {o['findings_summary']}\n"
                    f"Notes: {o['decision_notes'] or '(none)'}\n"
                )
        else:
            parts.append("## Recent feedback\nNo outcomes recorded yet — use your best judgment to improve clarity and actionability.\n")

        parts.append("\n## Instructions\nProduce the improved skill content now.")
        return "\n".join(parts)

    def _reconstruct_skill(self, skill: dict) -> str:
        """Rebuild a full .md file from a skill dict (for LLM input)."""
        front = ["---"]
        for key in ("name", "description", "trigger", "severity"):
            if skill.get(key):
                val = str(skill[key]).replace('"', '\\"')
                front.append(f'{key}: "{val}"')
        front.append("---")
        return "\n".join(front) + "\n\n" + skill.get("body", "")

    def _extract_skill_content(self, raw: str) -> str:
        """Pull the skill content out of the LLM response.

        Accepts:
        - Raw .md content (frontmatter present)
        - Content wrapped in ```markdown ... ``` fences
        - Content with leading/trailing prose
        """
        text = raw.strip()

        # Strip ```markdown ... ``` fences
        fence_match = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Must start with --- (frontmatter) for a valid skill file
        if not text.startswith("---"):
            # Maybe the LLM added some intro text; find the frontmatter
            fm_match = re.search(r"^---\s*\n.*?---\s*\n", text, re.DOTALL | re.MULTILINE)
            if fm_match:
                text = text[fm_match.start():].strip()

        return text


# ============================================================
# Convenience function
# ============================================================

_default_evolver: Optional[SkillEvolver] = None


def evolve_skill(skill_name: str, save: bool = False) -> str:
    """Evolve a skill using a default-constructed evolver (real LLM client)."""
    global _default_evolver
    if _default_evolver is None:
        from hermes.core.llm import TokenHubClient
        from hermes.config.settings import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
        client = TokenHubClient(
            api_key=LLM_API_KEY(),
            model=LLM_MODEL(),
            base_url=LLM_BASE_URL(),
        )
        _default_evolver = SkillEvolver(llm_client=client)
    return _default_evolver.evolve_skill(skill_name, save=save)
