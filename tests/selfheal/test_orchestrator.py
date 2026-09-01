"""Tests for hermes.selfheal.orchestrator (main flow).

编排器是确定性流程：探测→分级→执行/审批→验证→落库。
测试用 mock 隔离 SSH/凭据；落库使用隔离的测试 DB（OPS_DB_PATH）。
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["OPS_DB_PATH"] = "/tmp/opsticket_test/selfheal.db"
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from hermes.data import db, models  # noqa: E402
from hermes.selfheal import config, orchestrator  # noqa: E402


def _mock_exec(results):
    it = iter(results)

    def _f(server_id, command, timeout=10):
        return next(it)

    return patch("hermes.selfheal.actions.exec_ssh", side_effect=_f)


def _mock_get_server_args(server_id):
    return ("10.0.0.1", {"port": 22, "username": "root", "password": "x"}, "web-01")


class _SelfHealTestCase(unittest.TestCase):
    """公共 setUp：初始化测试 DB、清空自愈动作表、确保存在 Server(id=1)。"""

    def setUp(self):
        db.init_db()
        with db.session_scope() as s:
            s.query(models.SelfHealAction).delete()
            if not s.query(models.Server).first():
                cred = models.SSHCredential(name="c", username="root",
                                            password="x", port=22, is_default=True)
                s.add(cred)
                s.flush()
                s.add(models.Server(name="s", host="10.0.0.1",
                                    ssh_credential_id=cred.id))


class RunLowRiskDiskTests(_SelfHealTestCase):
    def setUp(self):
        super().setUp()
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        config.reload_config()

    def test_low_risk_auto_execute_and_verify_success(self):
        # 探测 85%(低危区间 [80,90)) → 自动执行 truncate_log → 验证 70% < 80 → verified
        with patch("hermes.selfheal.orchestrator.get_server_ssh_args",
                   side_effect=_mock_get_server_args), \
             _mock_exec([
                 {"success": True, "exit_code": 0,
                  "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                            "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
                 {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                 {"success": True, "exit_code": 0,
                  "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                            "/dev/vda1 ext4 50G 35G 15G 70% /\n", "stderr": ""},
             ]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/var/log/nginx/access.log"}, "user")
        self.assertEqual(result["severity"], "low")
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["success"])
        self.assertEqual(result["action_name"], "truncate_log")

    def test_high_risk_goes_to_pending(self):
        # 探测 96% ≥ 高危阈值 90 → 落审批单 pending，不执行
        with patch("hermes.selfheal.orchestrator.get_server_ssh_args",
                   side_effect=_mock_get_server_args), \
             _mock_exec([
                 {"success": True, "exit_code": 0,
                  "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                            "/dev/vda1 ext4 50G 48G 2G 96% /\n", "stderr": ""},
             ]):
            result = orchestrator.run_selfheal(1, "disk_clean", {"mount": "/"}, "user")
        self.assertEqual(result["severity"], "high")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["success"])
        self.assertIsNone(result.get("execution_result"))
        self.assertIn("action_id", result)

    def test_probe_failure_returns_failed(self):
        with patch("hermes.selfheal.orchestrator.get_server_ssh_args",
                   side_effect=_mock_get_server_args), \
             _mock_exec([{"success": False, "error": "SSH error: timeout"}]):
            result = orchestrator.run_selfheal(
                1, "disk_clean", {"mount": "/"}, "user")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])


class RunProcessTests(_SelfHealTestCase):
    def setUp(self):
        super().setUp()
        os.environ["SELFHEAL_SERVICE_WHITELIST"] = "nginx"
        # 关闭业务高峰(避免依赖系统当前时间导致 flaky) → 重启固定为低危自主执行
        os.environ["SELFHEAL_PEAK_HOURS"] = ""
        config.reload_config()

    def test_process_low_risk_restart(self):
        # 探测 failed(服务异常) → 低危 → restart nginx → 验证 active → verified
        with patch("hermes.selfheal.orchestrator.get_server_ssh_args",
                   side_effect=_mock_get_server_args), \
             _mock_exec([
                 {"success": True, "exit_code": 0, "stdout": "failed", "stderr": ""},
                 {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                 {"success": True, "exit_code": 0, "stdout": "active", "stderr": ""},
             ]):
            result = orchestrator.run_selfheal(
                1, "process_restart", {"service": "nginx"}, "dialog")
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["success"])
        self.assertEqual(result["action_name"], "restart_service")

    def test_verification_failed_means_success_false(self):
        with patch("hermes.selfheal.orchestrator.get_server_ssh_args",
                   side_effect=_mock_get_server_args), \
             _mock_exec([
                 {"success": True, "exit_code": 0, "stdout": "failed", "stderr": ""},
                 {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                 {"success": True, "exit_code": 0, "stdout": "failed", "stderr": ""},
             ]):
            result = orchestrator.run_selfheal(
                1, "process_restart", {"service": "nginx"}, "user")
        self.assertEqual(result["status"], "verification_failed")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
