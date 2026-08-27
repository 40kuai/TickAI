"""Tests for hermes.agents.skill_runner — executes a skill via the LLM."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Isolated test DB
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_runner.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Ensure skill K8s tools are registered
import hermes.tools.k8s.tools  # noqa: F401

from hermes.agents.skill_runner import (
    SkillRunner,
    SkillExecutionError,
    run_skill,
)
from hermes.skills.loader import save_skill
from hermes.data import db
from hermes.data.models import SkillOutcome


SAMPLE_SKILL = """\
---
name: test_skill
description: A test skill
trigger: manual
---

# Test Skill

Look at nodes. Flag those with MemoryPressure condition.
"""


class SkillRunnerUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skill_path = save_skill("test_skill", SAMPLE_SKILL, skills_dir=self.tmpdir)

        # Mock LLM client
        self.mock_llm = MagicMock()
        self.mock_llm.chat.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Found 2 problematic nodes: node-1, node-2",
                    "tool_calls": None,
                }
            }]
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_skill_persists_outcome(self):
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        outcome_id = runner.run("test_skill", cluster_context="prod", triggered_by="user")
        self.assertIsInstance(outcome_id, int)

        with db.session_scope() as s:
            outcome = s.get(SkillOutcome, outcome_id)
            self.assertIsNotNone(outcome)
            self.assertEqual(outcome.skill_name, "test_skill")
            self.assertEqual(outcome.cluster_context, "prod")
            self.assertEqual(outcome.triggered_by, "user")
            self.assertEqual(outcome.user_decision, "pending")

    def test_run_skill_captures_findings(self):
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        outcome_id = runner.run("test_skill")

        with db.session_scope() as s:
            outcome = s.get(SkillOutcome, outcome_id)
            self.assertIn("node-1", outcome.findings_summary)
            self.assertIn("node-2", outcome.findings_summary)

    def test_run_skill_passes_skill_body_to_llm(self):
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        runner.run("test_skill")
        # The LLM was called at least once
        self.mock_llm.chat.assert_called()
        # Inspect the messages sent to the LLM
        call_args = self.mock_llm.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        # system message should contain skill body
        system_msg = next((m for m in messages if m.get("role") == "system"), None)
        self.assertIsNotNone(system_msg)
        self.assertIn("MemoryPressure", system_msg["content"])

    def test_run_skill_includes_k8s_tools(self):
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        runner.run("test_skill")
        call_args = self.mock_llm.chat.call_args
        tools = call_args.kwargs.get("tools") or call_args[1].get("tools")
        tool_names = {t["function"]["name"] for t in tools}
        self.assertIn("check_k8s_nodes", tool_names)
        self.assertIn("check_k8s_pods", tool_names)
        self.assertIn("check_k8s_events", tool_names)

    def test_run_skill_skill_not_found(self):
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        with self.assertRaises(SkillExecutionError):
            runner.run("nonexistent_skill")

    def test_run_skill_handles_llm_error(self):
        self.mock_llm.chat.side_effect = RuntimeError("LLM unavailable")
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        with self.assertRaises(SkillExecutionError):
            runner.run("test_skill")

    def test_run_skill_executes_tool_calls_then_final(self):
        """LLM first returns a tool_call, then a final text answer."""
        self.mock_llm.chat.side_effect = [
            # Round 1: tool call
            {"choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "check_k8s_nodes",
                            "arguments": '{"context": "prod"}',
                        },
                    }],
                }
            }]},
            # Round 2: final answer
            {"choices": [{
                "message": {
                    "role": "assistant",
                    "content": "All 5 nodes healthy",
                    "tool_calls": None,
                }
            }]},
        ]
        # Patch the tool dispatcher to return a fake result
        with patch("hermes.agents.skill_runner.registry") as mock_reg:
            mock_reg.dispatch.return_value = '{"items": []}'
            mock_reg.list_schemas.return_value = [
                {"name": "check_k8s_nodes",
                 "description": "nodes",
                 "parameters": {"type": "object", "properties": {}}}
            ]
            runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
            outcome_id = runner.run("test_skill")
        # Tool was called
        mock_reg.dispatch.assert_called_with("check_k8s_nodes", {"context": "prod"})
        # Outcome captured final answer
        with db.session_scope() as s:
            outcome = s.get(SkillOutcome, outcome_id)
            self.assertIn("healthy", outcome.findings_summary)

    def test_run_skill_max_tool_rounds(self):
        """If the LLM keeps calling tools, runner should give up after max rounds."""
        # Always return a tool_call → infinite loop unless we cap
        self.mock_llm.chat.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "check_k8s_nodes", "arguments": "{}"},
                    }],
                }
            }]
        }
        with patch("hermes.agents.skill_runner.registry") as mock_reg:
            mock_reg.dispatch.return_value = '{"items": []}'
            mock_reg.list_schemas.return_value = []
            runner = SkillRunner(
                llm_client=self.mock_llm,
                skills_dir=self.tmpdir,
                max_tool_rounds=3,
            )
            outcome_id = runner.run("test_skill")
        # 3 calls to LLM (max rounds reached)
        self.assertEqual(self.mock_llm.chat.call_count, 3)
        with db.session_scope() as s:
            outcome = s.get(SkillOutcome, outcome_id)
            self.assertIn("max tool rounds", outcome.findings_summary.lower())


class ConvenienceFunctionTests(unittest.TestCase):
    def test_run_skill_function_exists(self):
        # Just verify the function is callable
        self.assertTrue(callable(run_skill))


if __name__ == "__main__":
    unittest.main()
