"""Tests for hermes.selfheal.config."""
import os
import unittest
from unittest.mock import patch

from hermes.config import settings
from hermes.selfheal import config


class LogCleanupConfigTests(unittest.TestCase):
    def setUp(self):
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,service,docker-log,docker-prune"
        config.reload_config()

    def test_default_categories_empty(self):
        # 隔离 .env 文件影响(settings 优先级: 环境变量 > .env > 默认),
        # 模拟"无任何配置"的纯默认态 → 白名单空=安全兜底。
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
            with patch.object(settings, "_file_env", {}):
                config.reload_config()
                self.assertEqual(config.LOG_CLEANUP_CATEGORIES, [])

    def test_categories_env(self):
        self.assertEqual(config.LOG_CLEANUP_CATEGORIES,
                         ["system", "service", "docker-log", "docker-prune"])

    def test_cleanup_sizes(self):
        self.assertEqual(config.JOURNAL_VACUUM_SIZE_MB, 200)
        self.assertEqual(config.SERVICE_LOG_MAX_MB, 100)
        self.assertEqual(config.SERVICE_LOG_MAX_DAYS, 7)
        self.assertEqual(config.DOCKER_LOG_MAX_MB, 50)

    def test_docker_log_prefix(self):
        self.assertTrue(config.DOCKER_LOG_TRUNCATE_PREFIX.startswith("/var/lib/docker/containers/"))


class ConfigTests(unittest.TestCase):
    def test_default_thresholds(self):
        self.assertEqual(config.DISK_LOW_PCT, 80)
        self.assertEqual(config.DISK_HIGH_PCT, 90)
        self.assertEqual(config.CACHE_LOW_PCT, 80)
        self.assertEqual(config.CACHE_HIGH_PCT, 90)

    def test_default_whitelists(self):
        self.assertIsInstance(config.SERVICE_WHITELIST, list)
        self.assertIsInstance(config.LOG_PATH_WHITELIST, list)
        self.assertIsInstance(config.DROPCACHES_MODES_WHITELIST, list)

    def test_peak_hours_default(self):
        self.assertEqual(config.PEAK_HOURS, [(9, 18)])

    def test_env_overrides_service_whitelist(self):
        # patch.dict 退出时只恢复环境变量，不恢复模块全局变量，
        # 因此在测试结束后重新加载配置，恢复模块级状态，避免污染其他测试。
        self.addCleanup(config.reload_config)
        with patch.dict(os.environ, {"SELFHEAL_SERVICE_WHITELIST": "nginx,mysql"}, clear=False):
            config.reload_config()
            self.assertIn("nginx", config.SERVICE_WHITELIST)
            self.assertIn("mysql", config.SERVICE_WHITELIST)


if __name__ == "__main__":
    unittest.main()
