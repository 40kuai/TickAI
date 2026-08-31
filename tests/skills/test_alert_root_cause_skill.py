"""Tests for the KR3 closed-loop troubleshooting skill.

Verifies the alert-driven root-cause analysis skill:
- can be loaded from the skills library
- references only tools that are really registered (no invented tools)
- covers the full closed loop: alert pull → metric analysis → root cause
  → recovery suggestion
"""
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_skill.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Ensure tools are registered so the "references real tools" check has
# the actual registry to compare against.
import hermes.tools  # noqa: F401
from hermes.skills.loader import load_skill, SKILLS_DIR, list_skills
from hermes.tools.registry import registry

SKILL_NAME = "diagnose_alert_root_cause"

# 正文里反引号包裹的 JSON 字段名 / 其它 skill 名 / 占位符 —— 不是工具引用。
# 工具引用检查只针对真正以工具形式出现的名字。
ALLOWED_NON_TOOL_REFS = {
    "events", "group_name", "is_recovered", "limit", "minutes", "no_data",
    "query", "recover_time", "rule_name", "service", "service_prefix",
    "severity", "trigger_time", "trigger_value",
    "diagnose_prometheus_anomaly",  # 引用的是另一个 skill，不是工具
}


def _tool_refs_in_body(body: str) -> set[str]:
    """Extract backtick-quoted tool names from a skill body."""
    refs = set()
    for m in re.finditer(r"`([a-z][a-z0-9_]+)`", body):
        refs.add(m.group(1))
    return refs


class SkillLoadableTests(unittest.TestCase):
    def test_skill_exists_in_library(self):
        names = {s["name"] for s in list_skills(SKILLS_DIR)}
        self.assertIn(SKILL_NAME, names)

    def test_skill_loads_with_metadata(self):
        skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.assertEqual(skill["name"], SKILL_NAME)
        self.assertTrue(skill["description"].strip())
        self.assertTrue(skill["body"].strip())

    def test_frontmatter_trigger_and_severity_present(self):
        skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.assertTrue(str(skill.get("trigger", "")).strip())
        self.assertTrue(str(skill.get("severity", "")).strip())


class ClosedLoopCompletenessTests(unittest.TestCase):
    """The KR3 skill must chain alert-pull → metric-analysis → root-cause
    → recovery-advice into one flow."""

    def setUp(self):
        self.skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.body = self.skill["body"]

    def test_has_alert_pull_stage(self):
        for kw in ("nightingale_history_alerts", "nightingale_active_alerts"):
            self.assertIn(kw, self.body, f"技能必须引用告警拉取工具 {kw}")

    def test_has_metric_analysis_stage(self):
        for kw in ("prometheus_service_health", "prometheus_metric_query"):
            self.assertIn(kw, self.body, f"技能必须引用指标分析工具 {kw}")

    def test_has_root_cause_and_recovery_stages(self):
        for kw in ("根因", "自愈", "建议"):
            self.assertIn(kw, self.body, f"技能必须包含环节关键词 {kw}")

    def test_describes_linked_steps_in_order(self):
        """The body should describe an ordered pipeline (步骤/流程)."""
        self.assertIn("步骤", self.body)


class ToolReferencesRealTests(unittest.TestCase):
    """Every backtick-quoted tool name in the body must be a real registered tool."""

    def setUp(self):
        self.skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.body = self.skill["body"]
        self.registered = {s["name"] for s in registry.list_schemas()}

    def test_all_tool_references_exist(self):
        refs = _tool_refs_in_body(self.body)
        refs -= ALLOWED_NON_TOOL_REFS
        unknown = refs - self.registered
        self.assertEqual(
            unknown,
            set(),
            f"技能引用了未注册的工具: {sorted(unknown)}",
        )

    def test_references_expected_tools(self):
        refs = _tool_refs_in_body(self.body)
        self.assertIn("nightingale_history_alerts", refs)
        self.assertIn("nightingale_active_alerts", refs)
        self.assertIn("prometheus_service_health", refs)


if __name__ == "__main__":
    unittest.main()
