"""Tests for hermes.selfheal.orchestrator (main flow).

编排器是确定性流程：探测→分级→执行/审批→验证→落库。
测试用 mock 隔离 SSH/凭据；落库使用隔离的测试 DB（OPS_DB_PATH，
由 tests/selfheal/conftest.py 统一配置）。
"""
import os
import unittest
from unittest.mock import patch

from hermes.data import db, models  # noqa: E402
from hermes.selfheal import config, orchestrator  # noqa: E402


def _mock_exec(results):
    it = iter(results)

    def _f(server_id, command, timeout=10):
        return next(it)

    return patch("hermes.selfheal.actions.exec_ssh", side_effect=_f)


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
        with _mock_exec([
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
        # DB 持久化断言：verified + success=True + 精确 truncate 命令
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).first()
            self.assertEqual(row.status, "verified")
            self.assertTrue(row.success)
            self.assertEqual(row.rendered_command, "truncate -s 0 /var/log/nginx/access.log")

    def test_high_risk_goes_to_pending(self):
        # 探测 96% ≥ 高危阈值 90 → 落审批单 pending，不执行
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 48G 2G 96% /\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/var/log/nginx/access.log"}, "user")
        self.assertEqual(result["severity"], "high")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["success"])
        self.assertIn("action_id", result)
        # DB 断言：pending 记录的分级理由已落库（grade_reasons 非空）
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="pending").first()
            self.assertIsNotNone(row)
            self.assertIsNotNone(row.grade_reasons)
            self.assertNotEqual(row.grade_reasons, "")

    def test_probe_failure_returns_failed(self):
        with _mock_exec([{"success": False, "error": "SSH error: timeout"}]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/var/log/nginx/access.log"}, "user")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        # DB 断言：存在 status=failed 的失败记录
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="failed").first()
            self.assertIsNotNone(row)

    def test_exec_failure_returns_failed_and_persists(self):
        # 探测 85% → 执行失败(exec_ssh success=False) → status=failed 且落库失败记录
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
                {"success": False, "error": "SSH error: command failed", "exit_code": 1,
                 "stdout": "", "stderr": "permission denied"},
        ]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/var/log/nginx/access.log"}, "user")
        self.assertEqual(result["severity"], "low")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="failed").first()
            self.assertIsNotNone(row)
            self.assertFalse(row.success)
            self.assertIn("command failed", row.execution_result or "")

    def test_render_failure_returns_failed_and_persists(self):
        # 渲染失败：path 不在白名单 → ValueError → status=failed 落库，rendered_command 为 None
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/tmp/not-whitelisted.log"}, "user")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="failed").first()
            self.assertIsNotNone(row)
            self.assertIsNone(row.rendered_command)
            self.assertIn("参数校验失败", row.grade_reasons or "")


class RunProcessTests(_SelfHealTestCase):
    def setUp(self):
        super().setUp()
        os.environ["SELFHEAL_SERVICE_WHITELIST"] = "nginx"
        # 关闭业务高峰(避免依赖系统当前时间导致 flaky) → 重启固定为低危自主执行
        os.environ["SELFHEAL_PEAK_HOURS"] = ""
        config.reload_config()

    def test_process_low_risk_restart(self):
        # 探测 failed(服务异常) → 低危 → restart nginx → 验证 active → verified
        with _mock_exec([
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
        with _mock_exec([
                {"success": True, "exit_code": 0, "stdout": "failed", "stderr": ""},
                {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                {"success": True, "exit_code": 0, "stdout": "failed", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "process_restart", {"service": "nginx"}, "user")
        self.assertEqual(result["status"], "verification_failed")
        self.assertFalse(result["success"])


class RunCacheTests(_SelfHealTestCase):
    def setUp(self):
        super().setUp()
        os.environ["SELFHEAL_DROPCACHES_MODES_WHITELIST"] = "1,2,3"
        config.reload_config()

    def test_cache_low_risk_success(self):
        # 探测缓存 85%(低危) → clean_cache → 验证 70% < 80 → verified
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "MemTotal:       2000000 kB\n"
                           "MemFree:         200000 kB\n"
                           "Buffers:         500000 kB\n"
                           "Cached:         1000000 kB\n"
                           "SReclaimable:    200000 kB\n", "stderr": ""},
                {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                {"success": True, "exit_code": 0,
                 "stdout": "MemTotal:       2000000 kB\n"
                           "MemFree:         800000 kB\n"
                           "Buffers:         300000 kB\n"
                           "Cached:          600000 kB\n"
                           "SReclaimable:    100000 kB\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "cache_clean", {"mode": "1"}, "user")
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["success"])
        self.assertEqual(result["action_name"], "clean_cache")


class CleanupSceneTests(_SelfHealTestCase):
    def setUp(self):
        super().setUp()
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,service,docker-log,docker-prune"
        config.reload_config()

    def test_log_cleanup_script_always_pending(self):
        # 磁盘 85%(低危区间), 但 log_cleanup_script 恒高危 → 落审批单, 不执行
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "log_cleanup_script", {"category": "system", "mount": "/"}, "user")
        self.assertEqual(result["severity"], "high")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["success"])
        # DB 断言：pending 记录已落库，分级理由含"人工审批"
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="pending").first()
            self.assertIsNotNone(row)
            self.assertIsNotNone(row.grade_reasons)
            self.assertIn("人工审批", row.grade_reasons)

    def test_log_cleanup_script_without_category_pending(self):
        # category 已非必填: 仅传 mount 即可触发 → 落审批单(默认 all)
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "log_cleanup_script", {"mount": "/"}, "user")
        self.assertEqual(result["severity"], "high")
        self.assertEqual(result["status"], "pending")
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="pending").first()
            self.assertIsNotNone(row)
            # 审批时以空 category 渲染 → 默认 all(rendered_command 为 None, 审批时才渲染)

    def test_verify_recovered_no_baseline_degrades_to_abs(self):
        # 无基线时退化为绝对阈值: <DISK_LOW_PCT 恢复, ≥DISK_LOW_PCT 未恢复
        ok = orchestrator.verify_recovered(
            "ai_log_cleanup", {"mount": "/"},
            "Filesystem Type Size Used Avail Use% Mounted on\n"
            "/dev/vda1 ext4 50G 35G 15G 70% /\n")
        self.assertTrue(ok)
        bad = orchestrator.verify_recovered(
            "ai_log_cleanup", {"mount": "/"},
            "Filesystem Type Size Used Avail Use% Mounted on\n"
            "/dev/vda1 ext4 50G 42G 8G 85% /\n")
        self.assertFalse(bad)

    def test_verify_recovered_disk_dropped(self):
        # ai_log_cleanup: 基线 92% → 执行后 70% (下降) → recovered
        recovered = orchestrator.verify_recovered(
            "ai_log_cleanup", {"mount": "/"},
            "Filesystem Type Size Used Avail Use% Mounted on\n"
            "/dev/vda1 ext4 50G 35G 15G 70% /\n",
            baseline_pct=92)
        self.assertTrue(recovered)

    def test_verify_recovered_disk_risen_false(self):
        # 执行后反而升高 → 未恢复
        recovered = orchestrator.verify_recovered(
            "ai_log_cleanup", {"mount": "/"},
            "Filesystem Type Size Used Avail Use% Mounted on\n"
            "/dev/vda1 ext4 50G 48G 2G 96% /\n",
            baseline_pct=92)
        self.assertFalse(recovered)

    def test_execute_and_verify_cleanup_baseline(self):
        # approve 路径: 基线探测(92%) → 执行脚本 → 复探(70%) → verified
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 46G 4G 92% /\n", "stderr": ""},  # baseline
                {"success": True, "exit_code": 0, "stdout": "[cleanup] done", "stderr": ""},  # exec
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 35G 15G 70% /\n", "stderr": ""},  # verify
        ]):
            record = orchestrator._persist(
                1, "ai_log_cleanup", {"mount": "/", "size": "200"}, "high",
                "journal_vacuum", "pending", "ai")
            row = orchestrator.execute_and_verify(
                record, {"mount": "/", "size": "200"}, "journalctl --vacuum-size=200M")
        self.assertEqual(row.status, "verified")
        self.assertTrue(row.success)


if __name__ == "__main__":
    unittest.main()
