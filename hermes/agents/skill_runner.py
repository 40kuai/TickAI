"""SkillRunner — executes a skill by combining LLM + K8s tools.

A skill is a Markdown file describing how to analyze a Kubernetes cluster.
The runner:
  1. Loads the skill .md
  2. Builds a system prompt from the skill body
  3. Calls the LLM with the prompt + K8s tools
  4. Loops through tool calls (up to max_tool_rounds)
  5. Captures the final findings
  6. Persists a SkillOutcome row

The LLM client is injected for testability. Production uses TokenHubClient
from hermes.core.llm.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from hermes.tools.registry import registry
from hermes.agents.base import BaseAgent, AgentContext

from hermes.data import db
from hermes.data.models import SkillOutcome
from hermes.skills.loader import load_skill, SKILLS_DIR


class SkillExecutionError(RuntimeError):
    """Raised when a skill cannot be executed."""


# Default skill-related tools (K8s read-only). The LLM uses these to gather data.
SKILL_TOOLSETS = {"k8s"}


# Language directives appended to the system prompt. The LLM is asked to
# produce text fields in the user's chosen language while keeping technical
# fields (namespace, pod, memory_limit, category, timestamps) as-is.
LANGUAGE_DIRECTIVES: dict[str, str] = {
    "en": (
        "## Output language\n"
        "Produce all **text** fields (recommendation, evidence, summary, etc.) in **English**. "
        "Keep technical fields (namespace, pod, memory_limit, category, timestamps) as-is — "
        "they are identifiers, not prose."
    ),
    "zh": (
        "## 输出语言\n"
        "所有**文本**字段（recommendation、evidence、summary 等）请用**中文**输出。\n"
        "技术字段（namespace、pod、memory_limit、category、时间戳）保持原样 —— "
        "它们是标识符不是自然语言。"
    ),
}


def _resolve_language(language: str) -> str:
    """Return a supported language code; fall back to English."""
    return language if language in LANGUAGE_DIRECTIVES else "en"


def _skill_tools() -> list[dict]:
    """Return only the K8s tools (not the SSH/LLM tools) for the LLM."""
    out = []
    for ts in SKILL_TOOLSETS:
        for s in registry.list_schemas_by_toolset(ts):
            out.append({"type": "function", "function": s})
    return out


class SkillRunner(BaseAgent):
    """Execute a single skill end-to-end (modular agent)."""

    name = "skill_runner"
    description = "Runs a Kubernetes analysis skill"

    def __init__(
        self,
        llm_client: Any,
        skills_dir: str | Path = SKILLS_DIR,
        max_tool_rounds: int = 5,
        language: str = "en",
    ):
        self.llm = llm_client
        self.skills_dir = Path(skills_dir)
        self.max_tool_rounds = max_tool_rounds
        self.language = _resolve_language(language)

    def run(
        self,
        context: AgentContext,
    ) -> AgentContext:
        """
        BaseAgent interface implementation.
        Expects context to have:
            - skill_name: str
            - cluster_context: str (optional)
            - triggered_by: str (optional)
            - language: str (optional)
        """
        # Get parameters from context
        skill_name = context.get("skill_name")
        cluster_context = context.get("cluster_context", "")
        triggered_by = context.get("triggered_by", "user")
        language = context.get("language", None)

        if not skill_name:
            raise ValueError("Context must contain 'skill_name'")

        # Execute skill
        outcome_id = self.execute_skill(
            skill_name=skill_name,
            cluster_context=cluster_context,
            triggered_by=triggered_by,
            language=language,
        )

        # Set results back to context
        context.set("skill_outcome_id", outcome_id)
        context.append_history(self.name, f"skill {skill_name} executed")
        return context

    def execute_skill(
        self,
        skill_name: str,
        cluster_context: str = "",
        triggered_by: str = "user",
        language: str | None = None,
    ) -> int:
        """
        Backward-compatible interface (original method signature).
        """
        lang = _resolve_language(language) if language is not None else self.language
        # 1. Load skill
        try:
            skill = load_skill(skill_name, self.skills_dir)
        except Exception as exc:
            raise SkillExecutionError(f"failed to load skill {skill_name!r}: {exc}") from exc

        # 2. Build messages
        system_prompt = (
            f"You are a Kubernetes analysis agent.\n\n"
            f"## Skill: {skill['name']}\n\n"
            f"{skill['body']}\n\n"
            f"## Important\n"
            f"- You have read-only access to the cluster via the provided tools.\n"
            f"- NEVER call any non-read tool — only the tools provided.\n"
            f"- Focus on the skill's specific analysis goal.\n"
            f"- Provide a concise summary of findings when done.\n\n"
            f"{LANGUAGE_DIRECTIVES[lang]}"
        )
        user_message = (
            f"Analyze the cluster{f' (context: {cluster_context})' if cluster_context else ''} "
            f"using the skill '{skill['name']}'."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        tools = _skill_tools()

        # 3. LLM loop
        try:
            final_text = self._run_llm_loop(messages, tools)
        except Exception as exc:
            raise SkillExecutionError(f"LLM execution failed: {exc}") from exc

        # 4. Persist outcome
        outcome_id = self._persist(
            skill_name=skill_name,
            cluster_context=cluster_context,
            triggered_by=triggered_by,
            findings_text=final_text,
        )
        return outcome_id

    def _run_llm_loop(self, messages: list[dict], tools: list[dict]) -> str:
        """Run the LLM, dispatching tool calls until a final text answer or max rounds."""
        for _round in range(self.max_tool_rounds):
            response = self.llm.chat(messages=messages, tools=tools)
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")
            messages.append(msg)

            if not tool_calls:
                # Final answer
                return msg.get("content") or ""

            # Dispatch each tool call
            for tc in tool_calls:
                fn = tc["function"]
                args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                try:
                    result = registry.dispatch(fn["name"], args)
                except Exception as exc:  # noqa: BLE001
                    result = json.dumps({"error": f"tool dispatch failed: {exc}"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
        return "(max tool rounds reached without a final answer)"

    def _persist(
        self,
        skill_name: str,
        cluster_context: str,
        triggered_by: str,
        findings_text: str,
    ) -> int:
        # Wrap findings in a JSON array so the JSON column is well-formed
        try:
            findings_obj = json.loads(findings_text)
            if not isinstance(findings_obj, list):
                findings_obj = [findings_obj]
        except (json.JSONDecodeError, TypeError):
            findings_obj = [{"summary": findings_text}]

        with db.session_scope() as s:
            outcome = SkillOutcome(
                skill_name=skill_name,
                skill_version=1,
                cluster_context=cluster_context,
                triggered_by=triggered_by,
                run_at=datetime.utcnow(),
                findings_json=json.dumps(findings_obj, ensure_ascii=False),
                findings_summary=findings_text[:2000],
                user_decision="pending",
            )
            s.add(outcome)
            s.flush()
            return outcome.id


# ============================================================
# Convenience function (uses default TokenHubClient)
# ============================================================

_default_runner: Optional[SkillRunner] = None


def run_skill(
    skill_name: str,
    cluster_context: str = "",
    triggered_by: str = "user",
) -> int:
    """Run a skill using a default-constructed runner (real LLM client)."""
    global _default_runner
    if _default_runner is None:
        from hermes.core.llm import TokenHubClient
        from hermes.config.settings import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
        client = TokenHubClient(
            api_key=LLM_API_KEY(),
            model=LLM_MODEL(),
            base_url=LLM_BASE_URL(),
        )
        _default_runner = SkillRunner(llm_client=client)
    return _default_runner.execute_skill(skill_name, cluster_context, triggered_by)
