"""Tests for hermes.selfheal.config."""
import os
import unittest
from unittest.mock import patch

from hermes.selfheal import config


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
        with patch.dict(os.environ, {"SELFHEAL_SERVICE_WHITELIST": "nginx,mysql"}, clear=False):
            config.reload_config()
            self.assertIn("nginx", config.SERVICE_WHITELIST)
            self.assertIn("mysql", config.SERVICE_WHITELIST)


if __name__ == "__main__":
    unittest.main()
