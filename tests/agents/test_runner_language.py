"""Extra tests for SkillRunner language parameter."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test_runner_lang.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

import hermes.tools.k8s.tools  # noqa: F401
from hermes.data import db
from hermes.agents.skill_runner import SkillRunner, LANGUAGE_DIRECTIVES
from hermes.skills.loader import save_skill

SAMPLE_SKILL = """\
---
name: test_skill
description: A test skill
trigger: manual
---

# Test Skill

Find problematic things.
"""


class LanguageDirectiveTests(unittest.TestCase):
    """The LANGUAGE_DIRECTIVES table must cover the supported languages."""

    def test_has_chinese_directive(self):
        self.assertIn("zh", LANGUAGE_DIRECTIVES)
        # Directive must contain the target language marker
        self.assertIn("中文", LANGUAGE_DIRECTIVES["zh"])

    def test_has_english_directive(self):
        self.assertIn("en", LANGUAGE_DIRECTIVES)
        self.assertIn("English", LANGUAGE_DIRECTIVES["en"])


class SkillRunnerLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        save_skill("test_skill", SAMPLE_SKILL, skills_dir=self.tmpdir)
        self.mock_llm = MagicMock()
        self.mock_llm.chat.return_value = {
            "choices": [{"message": {
                "role": "assistant",
                "content": "done",
                "tool_calls": None,
            }}]
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_system_prompt(self, **run_kwargs):
        runner = SkillRunner(llm_client=self.mock_llm, skills_dir=self.tmpdir)
        runner.run("test_skill", **run_kwargs)
        call = self.mock_llm.chat.call_args
        messages = call.kwargs.get("messages") or call[1].get("messages")
        return next(m["content"] for m in messages if m.get("role") == "system")

    def test_chinese_language_directive_in_prompt(self):
        prompt = self._get_system_prompt(language="zh")
        # Chinese directive marker
        self.assertIn("输出语言", prompt)
        # The full directive is appended
        self.assertIn(LANGUAGE_DIRECTIVES["zh"], prompt)

    def test_english_language_directive_in_prompt(self):
        prompt = self._get_system_prompt(language="en")
        self.assertIn(LANGUAGE_DIRECTIVES["en"], prompt)

    def test_default_language_is_english(self):
        # When no language passed, default to en
        prompt = self._get_system_prompt()
        self.assertIn(LANGUAGE_DIRECTIVES["en"], prompt)

    def test_invalid_language_falls_back_to_english(self):
        prompt = self._get_system_prompt(language="klingon")
        # Falls back to English directive
        self.assertIn(LANGUAGE_DIRECTIVES["en"], prompt)


if __name__ == "__main__":
    unittest.main()
