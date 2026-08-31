"""Tests for the optimized detect_oom_killed skill.

Verifies:
- the skill exists in the library and loads with metadata
- frontmatter trigger is user_initiated, severity is warning
- body is in Chinese and covers the key stages + tunable parameters
- every backtick-quoted tool name is a real registered tool
"""
import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_oom_skill.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Ensure tools are registered so the "references real tools" check has
# the actual registry to compare against.
import hermes.tools  # noqa: F401
from hermes.skills.loader import load_skill, SKILLS_DIR, list_skills
from hermes.tools.registry import registry

SKILL_NAME = "detect_oom_killed"

# 正文里反引号包裹的 JSON 字段名 / 占位符 —— 不是工具引用。
# 工具引用检查只针对真正以工具形式出现的名字。
ALLOWED_NON_TOOL_REFS = {
    "cluster_context", "conditions", "container_type", "context", "hours",
    "kubectl", "lastState", "limits", "memory_limit", "namespace", "note",
    "reason", "restartCount", "system",
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

    def test_frontmatter_trigger_is_user_initiated(self):
        skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.assertEqual(skill.get("trigger"), "user_initiated")

    def test_frontmatter_severity_is_warning(self):
        skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.assertEqual(skill.get("severity"), "warning")

    def test_description_is_chinese(self):
        skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.assertRegex(skill["description"], r"[\u4e00-\u9fff]")


class ContentCompletenessTests(unittest.TestCase):
    """The optimized body must be Chinese and cover key stages + tunable params."""

    def setUp(self):
        self.skill = load_skill(SKILL_NAME, SKILLS_DIR)
        self.body = self.skill["body"]

    def test_body_is_chinese(self):
        self.assertRegex(self.body, r"[\u4e00-\u9fff]")

    def test_has_key_stages(self):
        for kw in ("Goal", "When to use", "可用工具", "推荐流程", "分类与建议", "输出格式", "不要做什么"):
            self.assertIn(kw, self.body, f"技能必须包含章节 {kw}")

    def test_has_tunable_parameters_section(self):
        self.assertIn("可调参数", self.body)

    def test_has_tunable_parameter_keys(self):
        for kw in ("namespace", "hours", "context", "cluster_context"):
            self.assertIn(kw, self.body, f"技能必须说明可调参数 {kw}")

    def test_describes_oomkilling_event_filter(self):
        self.assertIn("OOMKilling", self.body)

    def test_categorizes_root_causes(self):
        for kw in ("根因", "分类", "建议"):
            self.assertIn(kw, self.body, f"技能必须包含环节关键词 {kw}")

    def test_output_language_chinese(self):
        self.assertIn("中文", self.body)


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
        self.assertIn("check_k8s_events", refs)
        self.assertIn("check_k8s_pods", refs)
        self.assertIn("check_k8s_nodes", refs)


if __name__ == "__main__":
    unittest.main()
