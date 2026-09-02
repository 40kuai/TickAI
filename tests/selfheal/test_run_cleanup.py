"""Tests for hermes.selfheal.run_cleanup (通道一固定脚本入口)."""
import os
import unittest

from hermes.selfheal import config, run_cleanup


class RunCleanupTests(unittest.TestCase):
    def setUp(self):
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,service,docker-log,docker-prune"
        config.reload_config()

    def tearDown(self):
        os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
        config.reload_config()

    def test_load_script_non_empty(self):
        script = run_cleanup.load_cleanup_script()
        self.assertIn("clean_system", script)
        self.assertIn("bash", script)  # shebang
        self.assertTrue(script.lstrip().startswith("#!"))

    def test_build_command_allowed(self):
        cmd = run_cleanup.build_cleanup_command("system")
        # 阈值以 env 前缀下发 + bash -s -- <category>
        self.assertIn("JOURNAL_VACUUM_SIZE_MB=", cmd)
        self.assertIn("bash -s -- system", cmd)

    def test_build_command_rejects_unknown(self):
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("rm -rf /")

    def test_build_command_defaults_to_all(self):
        # category 缺省/为空 → 默认 all(白名单非空时放行, 脚本内置聚合)
        cmd = run_cleanup.build_cleanup_command("")
        self.assertIn("bash -s -- all", cmd)
        cmd2 = run_cleanup.build_cleanup_command(None)
        self.assertIn("bash -s -- all", cmd2)

    def test_build_command_all_rejected_when_whitelist_empty(self):
        # 安全兜底: 白名单为空时连 all 也拒绝
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = ""
        config.reload_config()
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("")
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("all")

    def test_build_command_rejects_invalid_chars(self):
        # 白名单内但含非法字符(空格/分号) → 字符集校验拦截
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "a;rm -rf /,good"
        config.reload_config()
        with self.assertRaises(ValueError):
            run_cleanup.build_cleanup_command("a;rm -rf /")
        # 合法白名单成员仍可用
        self.assertIn("bash -s -- good", run_cleanup.build_cleanup_command("good"))
