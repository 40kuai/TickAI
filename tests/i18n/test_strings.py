"""Tests for hermes.i18n."""
import os
import unittest
from pathlib import Path

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from hermes.i18n import strings  # noqa: E402


class TranslationTests(unittest.TestCase):
    def test_t_zh_returns_chinese(self):
        self.assertEqual(i18n.t("home_title", lang="zh"),
                         "欢迎使用 OpsTicket")

    def test_t_en_returns_english(self):
        self.assertEqual(i18n.t("home_title", lang="en"),
                         "Welcome to OpsTicket")

    def test_t_falls_back_to_zh_for_unknown_lang(self):
        self.assertEqual(i18n.t("home_title", lang="xx"),
                         "欢迎使用 OpsTicket")

    def test_t_returns_key_if_missing(self):
        # Missing key in both languages → returns the key itself
        self.assertEqual(i18n.t("totally.nonexistent", lang="zh"),
                         "totally.nonexistent")
        self.assertEqual(i18n.t("totally.nonexistent", lang="en"),
                         "totally.nonexistent")

    def test_t_substitutes_kwargs(self):
        out = i18n.t("disk_executing", lang="zh", name="web-01")
        self.assertIn("web-01", out)
        self.assertIn("SSH", out)

    def test_t_kwargs_works_in_en(self):
        out = i18n.t("servers_count", lang="en", n=5)
        self.assertIn("5", out)
        self.assertIn("active", out)

    def test_t_kwargs_missing_does_not_crash(self):
        # If the template has a placeholder we didn't pass, just return raw
        out = i18n.t("disk_executing", lang="zh")  # missing 'name'
        self.assertIsInstance(out, str)


class EnumMappingTests(unittest.TestCase):
    def test_status_label_zh(self):
        self.assertEqual(i18n.status_label("success"), "成功")
        self.assertEqual(i18n.status_label("failed"), "失败")
        self.assertEqual(i18n.status_label("timeout"), "超时")
        self.assertEqual(i18n.status_label("ssh_error"), "SSH 错误")

    def test_source_label_zh(self):
        self.assertEqual(i18n.source_label("user_button"), "手动")
        self.assertEqual(i18n.source_label("llm_tool_call"), "LLM 触发")


class GetLangTests(unittest.TestCase):
    def test_default_lang_is_zh(self):
        # Without streamlit, get_lang() should return DEFAULT_LANG
        self.assertEqual(i18n.DEFAULT_LANG, "zh")
        # And get_lang() in a non-streamlit context returns zh
        self.assertEqual(i18n.get_lang(), "zh")

    def test_supported_languages(self):
        codes = [c for c, _ in i18n.SUPPORTED]
        self.assertIn("zh", codes)
        self.assertIn("en", codes)


if __name__ == "__main__":
    unittest.main()
