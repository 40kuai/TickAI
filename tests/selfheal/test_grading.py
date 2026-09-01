"""Tests for hermes.selfheal.grading (rule engine)."""
import unittest
from datetime import datetime

from hermes.selfheal import grading


def _grade(scene, metric, now=None):
    return grading.grade(scene, {"value": metric}, {"name": "web-01"}, now)


class DiskGradingTests(unittest.TestCase):
    def test_low_disk(self):
        g = _grade("disk_clean", 85)
        self.assertEqual(g["severity"], "low")
        self.assertTrue(g["can_auto"])

    def test_high_disk(self):
        g = _grade("disk_clean", 95)
        self.assertEqual(g["severity"], "high")
        self.assertFalse(g["can_auto"])

    def test_no_anomaly_below_low_threshold(self):
        g = _grade("disk_clean", 70)
        self.assertEqual(g["severity"], "ok")
        self.assertFalse(g["can_auto"])

    def test_boundary_90_high(self):
        # 锁 `>=` 语义：恰好等于高危阈值 90 → high，不可自动执行
        g = _grade("disk_clean", 90)
        self.assertEqual(g["severity"], "high")
        self.assertFalse(g["can_auto"])

    def test_boundary_80_low(self):
        # 锁 `>=` 语义：恰好等于低危阈值 80 → low，可自动执行
        g = _grade("disk_clean", 80)
        self.assertEqual(g["severity"], "low")
        self.assertTrue(g["can_auto"])


class CacheGradingTests(unittest.TestCase):
    def test_low_cache(self):
        g = _grade("cache_clean", 85)
        self.assertEqual(g["severity"], "low")

    def test_high_cache(self):
        g = _grade("cache_clean", 95)
        self.assertEqual(g["severity"], "high")

    def test_boundary_90_high(self):
        # 锁 `>=` 语义：恰好等于高危阈值 90 → high
        g = _grade("cache_clean", 90)
        self.assertEqual(g["severity"], "high")

    def test_boundary_80_low(self):
        # 锁 `>=` 语义：恰好等于低危阈值 80 → low
        g = _grade("cache_clean", 80)
        self.assertEqual(g["severity"], "low")


class ProcessGradingTests(unittest.TestCase):
    def test_low_process(self):
        # 注入非高峰时刻，避免依赖系统当前时间造成 flaky（白天高峰时段会误判为 high）
        g = _grade("process_restart", None, now=datetime(2026, 9, 1, 23, 0))
        self.assertEqual(g["severity"], "low")

    def test_peak_hour_forced_high(self):
        g = grading.grade(
            "process_restart", {"value": None}, {"name": "web-01"},
            datetime(2026, 9, 1, 10, 0),  # 业务高峰 9-18
        )
        self.assertEqual(g["severity"], "high")
        self.assertFalse(g["can_auto"])

    def test_off_peak_low(self):
        g = grading.grade(
            "process_restart", {"value": None}, {"name": "web-01"},
            datetime(2026, 9, 1, 23, 0),
        )
        self.assertEqual(g["severity"], "low")

    def test_peak_hour_boundary_9h(self):
        # 半开区间 [9,18)：9 时含端点 → 高峰 → high
        g = grading.grade(
            "process_restart", {"value": None}, {"name": "web-01"},
            datetime(2026, 9, 1, 9, 0),
        )
        self.assertEqual(g["severity"], "high")
        self.assertFalse(g["can_auto"])

    def test_off_peak_boundary_18h(self):
        # 半开区间 [9,18)：18 时不含端点 → 非高峰 → low
        g = grading.grade(
            "process_restart", {"value": None}, {"name": "web-01"},
            datetime(2026, 9, 1, 18, 0),
        )
        self.assertEqual(g["severity"], "low")
        self.assertTrue(g["can_auto"])

    def test_reasons_populated(self):
        g = _grade("disk_clean", 96)
        self.assertTrue(g["reasons"])
        self.assertIn("96", " ".join(g["reasons"]))


if __name__ == "__main__":
    unittest.main()
