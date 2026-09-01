"""Tests for hermes.tools.selfheal.inventory — scan_log_cleanup 只读扫描."""
import json
import unittest
from unittest.mock import patch

from hermes.tools.selfheal import inventory


DF_OUT = (
    "Filesystem Type Size Used Avail Use% Mounted on\n"
    "/dev/vda1 ext4 99G 84G 16G 85% /\n"
)
JOURNAL_OUT = "Archived and active journals take up 1.2G in the file system.\n"
VARLOG_OUT = "/var/log/messages\n/var/log/messages-20260801.gz\n"
SERVICE_OUT = "/data/nfc/logs/app.log\n/data/api/logs/error.log\n"
DOCKERLOG_OUT = "/var/lib/docker/containers/abc/abc-json.log\n"
DOCKERPS_OUT = "abc gitlab exited 2026-06-24\n"
DOCKERIMG_OUT = "REPOSITORY TAG IMAGE ID CREATED SIZE\n<none> <none> sha256:1 2 months ago 5GB\n"


class _MockExec:
    def __init__(self, results):
        self.it = iter(results)

    def __call__(self, server_id, command, timeout=10):
        return next(self.it)


class ScanInventoryTests(unittest.TestCase):
    def test_build_probe_commands(self):
        cmds = inventory._build_probe_commands()
        joined = "\n".join(cmds)
        self.assertIn("df -Th /", joined)
        self.assertIn("journalctl --disk-usage", joined)
        self.assertIn("find /var/lib/docker/containers", joined)

    def test_parse_full(self):
        parsed = inventory._parse_inventory({
            "df": DF_OUT, "journal": JOURNAL_OUT, "var_log": VARLOG_OUT,
            "service": SERVICE_OUT, "docker_log": DOCKERLOG_OUT,
            "docker_ps": DOCKERPS_OUT, "docker_images": DOCKERIMG_OUT,
        })
        self.assertEqual(parsed["disk"]["/"]["use_pct"], 85)
        self.assertEqual(parsed["journal"]["disk_used"], "1.2G")
        self.assertEqual(len(parsed["var_log"]), 2)
        self.assertEqual(len(parsed["service_logs"]), 2)
        self.assertEqual(parsed["docker_logs"][0]["path"],
                         "/var/lib/docker/containers/abc/abc-json.log")
        self.assertEqual(parsed["docker_containers"][0]["name"], "abc")
        self.assertGreaterEqual(parsed["docker_images"]["dangling"], 1)

    def test_scan_log_cleanup_handler(self):
        results = [
            {"success": True, "exit_code": 0, "stdout": DF_OUT, "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": JOURNAL_OUT, "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": VARLOG_OUT, "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": SERVICE_OUT, "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": DOCKERLOG_OUT, "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": DOCKERPS_OUT, "stderr": ""},
            {"success": True, "exit_code": 0, "stdout": DOCKERIMG_OUT, "stderr": ""},
        ]
        with patch("hermes.tools.selfheal.inventory.actions.exec_ssh",
                   side_effect=_MockExec(results)):
            out = inventory.scan_log_cleanup_handler({"server_id": 1})
        data = json.loads(out)
        self.assertEqual(data["server_id"], 1)
        self.assertIn("disk", data)

    def test_handler_missing_server_id(self):
        out = inventory.scan_log_cleanup_handler({})
        self.assertIn("error", json.loads(out))


if __name__ == "__main__":
    unittest.main()
