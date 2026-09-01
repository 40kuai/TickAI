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
    # Output format 里的 JSON 字段名（不是工具引用）
    "alerts", "deployment", "job_name", "metric_findings",
    "recovery_suggestions", "root_cause", "endpoint",
    # 工具参数名（不是工具引用）
    "start", "end",
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

    def test_has_deployment_change_correlation(self):
        """闭环技能必须包含「发布变更关联」环节：发布变更是故障最常见根因之一。
        Jenkins 发布记录工具已注册在 monitoring toolset，技能应引用它们做
        告警时间窗内的发布关联，判断是否由某次发布触发。"""
        for kw in ("jenkins_service_jobs", "jenkins_build_records"):
            self.assertIn(kw, self.body, f"技能必须引用发布变更查询工具 {kw}")
        # 需有"发布"相关的关联语义关键词
        self.assertTrue(
            any(kw in self.body for kw in ("发布", "变更", "回滚")),
            "技能必须包含发布/变更/回滚关联语义",
        )

    def test_has_alert_consolidation(self):
        """闭环技能必须包含「告警收敛」策略：同服务多规则、跨服务共享依赖的
        并发告警应归并为单一根因，避免输出割裂报告。"""
        self.assertIn("收敛", self.body, "技能必须包含「收敛」策略")
        self.assertTrue(
            any(kw in self.body for kw in ("同服务多规则", "共享依赖", "同源", "归并")),
            "技能必须包含多规则/共享依赖归并语义",
        )

    def test_has_actionable_recovery_check(self):
        """闭环技能必须包含「自愈建议可执行性校验」：每条建议须具备对象/定位/动作
        三要素，无法满足时标注 needs_human 兜底，不得假装可执行。"""
        self.assertIn("needs_human", self.body, "技能必须提供 needs_human 兜底标注")
        self.assertTrue(
            any(kw in self.body for kw in ("对象", "定位", "动作", "三要素")),
            "技能必须定义建议的可执行要素",
        )

    def test_has_time_window_alignment(self):
        """闭环技能必须对齐时间窗口：指标查询须用告警 trigger_time 对应的
        start/end（而非默认最近 10 分钟），否则告警发生时的指标状态查不到，
        会误判"现在正常"。"""
        self.assertTrue(
            any(kw in self.body for kw in ("trigger_time", "时间窗", "对齐", "窗口")),
            "技能必须包含时间窗口对齐语义",
        )
        # 指标查询工具须显式带 start/end
        self.assertIn("start", self.body, "技能必须说明指标查询传 start")
        self.assertIn("end", self.body, "技能必须说明指标查询传 end")


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
