"""Tests for hermes.agents.skill_evolver — LLM-driven skill improvement."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_evolver.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Register K8s tools
import hermes.tools.k8s.tools  # noqa: F401

from hermes.data import db
from hermes.agents.skill_evolver import (
    SkillEvolver,
    EvolutionError,
    evolve_skill,
)
from hermes.skills.loader import save_skill
from hermes.data.models import SkillOutcome, SkillVersion


INITIAL_SKILL = """\
---
name: evolve_me
description: A skill we'll evolve
trigger: manual
---

# Original Instructions

Use check_k8s_nodes. Find problematic ones.
"""


class SkillEvolverUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skill_path = save_skill("evolve_me", INITIAL_SKILL, skills_dir=self.tmpdir)

        # Add some outcome history (2 accepted, 1 rejected)
        with db.session_scope() as s:
            for i, decision in enumerate(["accepted", "accepted", "rejected"]):
                s.add(SkillOutcome(
                    skill_name="evolve_me",
                    cluster_context="prod",
                    triggered_by="user",
                    run_at=datetime.utcnow(),
                    findings_json=json.dumps([{"pod": f"pod-{i}"}]),
                    findings_summary=f"finding {i}",
                    user_decision=decision,
                    decision_at=datetime.utcnow(),
                    decision_notes=f"feedback {i}",
                ))

        # Mock LLM
        self.mock_llm = MagicMock()
        self.mock_llm.chat.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": (
                        "---\n"
                        "name: evolve_me\n"
                        "description: Improved version\n"
                        "---\n\n"
                        "# Improved Instructions\n\n"
                        "Use check_k8s_nodes AND check_k8s_events. Find problematic ones."
                    ),
                    "tool_calls": None,
                }
            }]
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_evolver_produces_new_content(self):
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        new_content = evolver.evolve("evolve_me")
        self.assertIn("Improved", new_content)
        self.assertIn("check_k8s_events", new_content)

    def test_evolver_passes_history_to_llm(self):
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        evolver.evolve("evolve_me")
        # LLM was called
        self.mock_llm.chat.assert_called()
        # Check messages contain the outcomes
        call_args = self.mock_llm.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_msg = next((m for m in messages if m.get("role") == "user"), None)
        self.assertIn("accepted", user_msg["content"])
        self.assertIn("rejected", user_msg["content"])

    def test_evolver_saves_new_content(self):
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        evolver.evolve("evolve_me", save=True)
        # File should be updated
        updated = (Path(self.tmpdir) / "evolve_me.md").read_text()
        self.assertIn("Improved", updated)

    def test_evolver_records_version(self):
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        evolver.evolve("evolve_me", save=True)
        with db.session_scope() as s:
            versions = s.query(SkillVersion).filter_by(skill_name="evolve_me").all()
            # At least 2: initial + evolved
            self.assertGreaterEqual(len(versions), 2)
            latest = max(versions, key=lambda v: v.version)
            self.assertEqual(latest.reason, "auto_evolve")
            self.assertIn("Improved", latest.content)

    def test_evolver_skill_not_found(self):
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        with self.assertRaises(EvolutionError):
            evolver.evolve("nonexistent_skill")

    def test_evolver_handles_llm_error(self):
        self.mock_llm.chat.side_effect = RuntimeError("LLM down")
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        with self.assertRaises(EvolutionError):
            evolver.evolve("evolve_me")

    def test_evolver_with_no_history_returns_original(self):
        # Make a new skill with no outcomes
        save_skill("fresh", INITIAL_SKILL.replace("evolve_me", "fresh"),
                   skills_dir=self.tmpdir)
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        # With no feedback, evolver should still consult LLM but may return same
        new_content = evolver.evolve("fresh")
        # The mock LLM will return its canned response
        self.assertIsInstance(new_content, str)
        self.assertGreater(len(new_content), 0)

    def test_evolver_extracts_content_from_markdown_response(self):
        """The LLM may return extra text around the markdown; we extract the body."""
        self.mock_llm.chat.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": (
                        "Here is the improved version:\n\n"
                        "---\nname: evolve_me\ndescription: v2\n---\n\n"
                        "# Improved Body\n\nBetter instructions.\n\n"
                        "Let me know if you need more changes."
                    ),
                    "tool_calls": None,
                }
            }]
        }
        evolver = SkillEvolver(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        new_content = evolver.evolve("evolve_me")
        self.assertIn("name: evolve_me", new_content)
        self.assertIn("Better instructions", new_content)


class ConvenienceFunctionTests(unittest.TestCase):
    def test_evolve_skill_function_exists(self):
        self.assertTrue(callable(evolve_skill))


if __name__ == "__main__":
    unittest.main()
