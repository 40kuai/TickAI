"""Tests for hermes.skills.loader."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Use isolated DB for tests
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_skill.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from hermes.skills import loader as skill_loader
from hermes.skills.loader import (
    list_skills,
    load_skill,
    save_skill,
    SkillLoadError,
    SKILLS_DIR,
)


SAMPLE_SKILL_1 = """\
---
name: detect_oom_killed
description: Find pods killed by OOMKiller in last 24h
trigger: scheduled_daily
severity: warning
---

# Detect OOMKilled Pods

Use check_k8s_events_for_context with type=Warning and reason=OOMKilling.
Cross-reference with pods that have NO memory limit set.
"""

SAMPLE_SKILL_2 = """\
---
name: find_idle_nodes
description: Find nodes with low utilization
---

# Find Idle Nodes

Look at node status.allocatable vs status.capacity.
Flag nodes with < 20% CPU usage in last 1h.
"""

SAMPLE_NO_FRONTMATTER = """\
# Just a body, no frontmatter
This skill has no metadata.
"""


class ListSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_dir_returns_empty_list(self):
        self.assertEqual(list_skills(self.tmpdir), [])

    def test_lists_skills_in_dir(self):
        (Path(self.tmpdir) / "detect_oom.md").write_text(SAMPLE_SKILL_1)
        (Path(self.tmpdir) / "find_idle.md").write_text(SAMPLE_SKILL_2)
        skills = list_skills(self.tmpdir)
        names = {s["name"] for s in skills}
        self.assertEqual(names, {"detect_oom_killed", "find_idle_nodes"})

    def test_skips_non_md_files(self):
        (Path(self.tmpdir) / "skill.md").write_text(SAMPLE_SKILL_1)
        (Path(self.tmpdir) / "readme.txt").write_text("not a skill")
        (Path(self.tmpdir) / "config.yaml").write_text("name: foo")
        skills = list_skills(self.tmpdir)
        self.assertEqual(len(skills), 1)


class LoadSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        (Path(self.tmpdir) / "detect_oom.md").write_text(SAMPLE_SKILL_1)
        (Path(self.tmpdir) / "raw.md").write_text(SAMPLE_NO_FRONTMATTER)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_specific_skill(self):
        skill = load_skill("detect_oom_killed", self.tmpdir)
        self.assertEqual(skill["name"], "detect_oom_killed")
        self.assertIn("OOMKiller", skill["description"])
        self.assertEqual(skill["trigger"], "scheduled_daily")
        self.assertEqual(skill["severity"], "warning")

    def test_load_skill_includes_body(self):
        skill = load_skill("detect_oom_killed", self.tmpdir)
        self.assertIn("check_k8s_events_for_context", skill["body"])
        self.assertIn("memory limit", skill["body"])

    def test_load_missing_skill_raises(self):
        with self.assertRaises(SkillLoadError):
            load_skill("nonexistent", self.tmpdir)

    def test_skill_without_frontmatter_uses_filename(self):
        skill = load_skill("raw", self.tmpdir)
        self.assertEqual(skill["name"], "raw")
        # description should be empty when no frontmatter
        self.assertEqual(skill["description"], "")

    def test_returns_full_path(self):
        skill = load_skill("detect_oom_killed", self.tmpdir)
        self.assertIn("detect_oom.md", skill["path"])


class SaveSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_creates_file(self):
        path = save_skill("new_skill",
                          "---\nname: new_skill\ndescription: A new one\n---\n\n# Body",
                          skills_dir=self.tmpdir)
        self.assertTrue(Path(path).exists())
        content = Path(path).read_text()
        self.assertIn("name: new_skill", content)
        self.assertIn("# Body", content)

    def test_save_overwrites_existing(self):
        save_skill("foo", "v1 content", skills_dir=self.tmpdir)
        save_skill("foo", "v2 content", skills_dir=self.tmpdir)
        skills = list_skills(self.tmpdir)
        self.assertEqual(len(skills), 1)
        loaded = load_skill("foo", self.tmpdir)
        self.assertIn("v2 content", loaded["body"])


class FrontmatterParsingTests(unittest.TestCase):
    """Test the YAML frontmatter parser independently of file I/O."""

    def test_parses_basic_frontmatter(self):
        from hermes.skills.loader import _parse_skill
        meta, body = _parse_skill(SAMPLE_SKILL_1)
        self.assertEqual(meta["name"], "detect_oom_killed")
        self.assertEqual(meta["trigger"], "scheduled_daily")
        self.assertIn("check_k8s_events_for_context", body)

    def test_handles_no_frontmatter(self):
        from hermes.skills.loader import _parse_skill
        meta, body = _parse_skill(SAMPLE_NO_FRONTMATTER)
        self.assertEqual(meta, {})
        self.assertIn("Just a body", body)

    def test_handles_empty(self):
        from hermes.skills.loader import _parse_skill
        meta, body = _parse_skill("")
        self.assertEqual(meta, {})
        self.assertEqual(body, "")


if __name__ == "__main__":
    unittest.main()
