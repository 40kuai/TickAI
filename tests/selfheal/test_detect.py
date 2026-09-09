"""Tests for hermes.selfheal.detect (pure parsers)."""
import unittest

from hermes.selfheal import detect


class ParseSystemctlTests(unittest.TestCase):
    def test_active(self):
        self.assertTrue(detect.parse_systemctl_active("active"))
        self.assertTrue(detect.parse_systemctl_active("active\n"))
        self.assertTrue(detect.parse_systemctl_active("  active  "))

    def test_inactive_and_failed(self):
        self.assertFalse(detect.parse_systemctl_active("inactive"))
        self.assertFalse(detect.parse_systemctl_active("failed"))
        self.assertFalse(detect.parse_systemctl_active(""))


class ParseDfTests(unittest.TestCase):
    DF_SAMPLE = (
        "Filesystem      Type  Size  Used Avail Use% Mounted on\n"
        "/dev/vda1       ext4   50G   45G  3.4G  94% /\n"
        "/dev/vdb1       ext4  200G   150G   50G  75% /data\n"
    )

    def test_finds_mount(self):
        pct = detect.parse_df_usage(self.DF_SAMPLE, "/")
        self.assertEqual(pct, 94)

    def test_mount_not_found_returns_none(self):
        self.assertIsNone(detect.parse_df_usage(self.DF_SAMPLE, "/nonexistent"))

    def test_empty_input(self):
        self.assertIsNone(detect.parse_df_usage("", "/"))


class ParseMeminfoTests(unittest.TestCase):
    def test_parses_cache_ratio(self):
        sample = (
            "MemTotal:       1000000 kB\n"
            "MemFree:         100000 kB\n"
            "Buffers:         100000 kB\n"
            "Cached:          300000 kB\n"
            "SReclaimable:     50000 kB\n"
        )
        info = detect.parse_meminfo(sample)
        self.assertEqual(info["mem_total"], 1000000)
        self.assertEqual(info["cache_kb"], 450000)  # Buffers+Cached+SReclaimable
        self.assertAlmostEqual(info["pct"], 45.0, places=1)

    def test_missing_fields(self):
        info = detect.parse_meminfo("MemTotal:1000 kB\n")
        self.assertEqual(info["cache_kb"], 0)
        self.assertIsNone(info["pct"])

    def test_empty_input(self):
        info = detect.parse_meminfo("")
        self.assertIsNone(info["pct"])


class ProbeCommandTests(unittest.TestCase):
    def test_process_probe(self):
        cmd = detect.probe_command("process_restart", {"service": "nginx"})
        # systemd 单元优先, 无该单元时 docker inspect 兜底(容器化服务), 都无 → unknown
        self.assertIn("systemctl is-active nginx", cmd)
        self.assertIn("docker inspect", cmd)
        self.assertIn("unknown", cmd)

    def test_disk_probe(self):
        cmd = detect.probe_command("disk_clean", {"mount": "/"})
        self.assertEqual(cmd, "df -Th /")

    def test_cache_probe(self):
        cmd = detect.probe_command("cache_clean", {"mode": "3"})
        self.assertEqual(cmd, "cat /proc/meminfo")

    def test_unknown_scene_raises(self):
        with self.assertRaises(ValueError):
            detect.probe_command("unknown_scene", {})


if __name__ == "__main__":
    unittest.main()
