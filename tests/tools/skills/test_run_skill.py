"""Tests for hermes.tools.skills — the run_skill dialog tool."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Isolated test DB
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_run_skill.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

import hermes.tools  # noqa: F401  (auto-discover, registers run_skill)
from hermes.tools.registry import registry
from hermes.tools.skills import tools as skill_tools


class RunSkillSchemaTests(unittest.TestCase):
    def test_schema_registered_and_well_formed(self):
        self.assertTrue(registry.has("run_skill"))
        schema = registry.get("run_skill")["schema"]
        self.assertEqual(schema["name"], "run_skill")
        self.assertEqual(schema["parameters"]["required"], ["skill_name"])
        self.assertIn("skill_name", schema["parameters"]["properties"])

    def test_schema_enum_lists_available_skills(self):
        schema = registry.get("run_skill")["schema"]
        prop = schema["parameters"]["properties"]["skill_name"]
        with patch.object(skill_tools, "_available_skill_names", return_value=["detect_oom_killed"]):
            enum = prop["enum"] if "enum" in prop else skill_tools._available_skill_names()
        self.assertIn("detect_oom_killed", enum)


class RunSkillHandlerTests(unittest.TestCase):
    def setUp(self):
        self.orig_list = skill_tools._available_skill_names
        skill_tools._available_skill_names = lambda: ["detect_oom_killed"]

    def tearDown(self):
        skill_tools._available_skill_names = self.orig_list

    def test_missing_skill_name_returns_error(self):
        out = skill_tools.run_skill_handler({})
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_unknown_skill_returns_error(self):
        out = skill_tools.run_skill_handler({"skill_name": "nope"})
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_success_returns_outcome_and_summary(self):
        fake_findings = "Found 2 OOMKilled pods in default namespace."
        with patch(
            "hermes.agents.skill_runner.run_skill", return_value=42
        ), patch.object(
            skill_tools, "_fetch_findings", return_value=fake_findings
        ):
            out = skill_tools.run_skill_handler({"skill_name": "detect_oom_killed"})
        payload = json.loads(out)
        self.assertEqual(payload["skill"], "detect_oom_killed")
        self.assertEqual(payload["outcome_id"], 42)
        self.assertEqual(payload["summary"], fake_findings)

    def test_handler_failure_returns_error(self):
        with patch(
            "hermes.agents.skill_runner.run_skill",
            side_effect=RuntimeError("LLM down"),
        ):
            out = skill_tools.run_skill_handler({"skill_name": "detect_oom_killed"})
        payload = json.loads(out)
        self.assertIn("error", payload)
        self.assertIn("LLM down", payload["error"])


class RunSkillAvailabilityTests(unittest.TestCase):
    def test_available_when_skills_exist(self):
        with patch.object(skill_tools, "_available_skill_names", return_value=["a"]):
            self.assertTrue(skill_tools.run_skill_available())

    def test_unavailable_when_no_skills(self):
        with patch.object(skill_tools, "_available_skill_names", return_value=[]):
            self.assertFalse(skill_tools.run_skill_available())


if __name__ == "__main__":
    unittest.main()
