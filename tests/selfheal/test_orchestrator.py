"""Tests for hermes.selfheal.orchestrator (main flow).

编排器是确定性流程：探测→分级→执行/审批→验证→落库。
测试用 mock 隔离 SSH/凭据；落库使用隔离的测试 DB（OPS_DB_PATH，
由 tests/selfheal/conftest.py 统一配置）。
"""
import json
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

    def tearDown(self):
        # 清理方法内设置的服务白名单, 防跨用例污染
        os.environ.pop("SELFHEAL_SERVICE_WHITELIST", None)
        config.reload_config()

    def test_low_risk_auto_execute_and_verify_success(self):
        # 探测 85%(低危区间) + AI 判定 auto + 影响面已确认(单文件) → 自动执行 truncate_log → 验证 70% < 80 → verified
        import hermes.selfheal.approval as approval_mod

        def _fake_judge(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
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
        # 探测 96% ≥ 高危阈值 90 → AI 判定不可用 fail-closed → 落审批单 pending，不执行
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
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

    def test_probe_failure_includes_stderr_and_persists_full_execution(self):
        # 探测命令 exit_code≠0(exec_ssh 无 error 键) → reason 含 stderr/exit_code, execution_result 落库完整
        with _mock_exec([{"success": False, "exit_code": 1,
                          "stderr": "Unit nginx.service could not be found.",
                          "stdout": ""}]):
            result = orchestrator.run_selfheal(
                1, "process_restart", {"service": "nginx"}, "user")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        self.assertIn("nginx.service could not be found", result["reason"])
        self.assertIn("exit_code=1", result["reason"])
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="failed").first()
            self.assertIsNotNone(row)
            exec_json = json.loads(row.execution_result)
            self.assertEqual(exec_json["exit_code"], 1)
            self.assertIn("nginx.service could not be found", exec_json["stderr"])

    def test_probe_inactive_service_continues_selfheal(self):
        # process_restart: systemctl is-active 对 inactive 服务返回 exit≠0, 这是"服务异常"信号
        # 而非探测失败 → 应继续自愈(审批/执行), 而不是落 failed
        os.environ["SELFHEAL_SERVICE_WHITELIST"] = "nginx"
        config.reload_config()
        import hermes.selfheal.approval as approval_mod

        def _fake_judge(payload):
            return {"risk_score": 5, "recommendation": "approval", "reasons": ["服务未运行, 需确认重启"]}

        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
            with _mock_exec([{"success": False, "exit_code": 3,
                              "stdout": "inactive", "stderr": ""}]):
                result = orchestrator.run_selfheal(
                    1, "process_restart", {"service": "nginx"}, "user")
        self.assertNotEqual(result["status"], "failed")
        self.assertEqual(result["status"], "pending")
        self.assertIn("需人工审批", result["message"])

    def test_probe_unknown_state_continues_selfheal(self):
        # CentOS7(systemd 219): systemctl is-active 对停止(非 active)服务返回 "unknown"+exit≠0,
        # 这是"服务异常"信号而非单元不存在 → 应继续自愈(审批/执行), 而不是落 failed
        os.environ["SELFHEAL_SERVICE_WHITELIST"] = "nginx"
        config.reload_config()
        import hermes.selfheal.approval as approval_mod

        def _fake_judge(payload):
            return {"risk_score": 5, "recommendation": "approval", "reasons": ["服务未运行, 需确认重启"]}

        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
            with _mock_exec([{"success": False, "exit_code": 3,
                              "stdout": "unknown", "stderr": ""}]):
                result = orchestrator.run_selfheal(
                    1, "process_restart", {"service": "nginx"}, "user")
        self.assertNotEqual(result["status"], "failed")
        self.assertEqual(result["status"], "pending")
        self.assertIn("需人工审批", result["message"])

    def test_exec_failure_returns_failed_and_persists(self):
        # 探测 85% + AI 判定 auto → 执行失败(exec_ssh success=False) → status=failed 且落库失败记录
        import hermes.selfheal.approval as approval_mod

        def _fake_judge(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
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

    def test_render_failure_rejected_and_persists(self):
        # 渲染失败：path 不在白名单 → hard_rule reject → status=rejected 落库，rendered_command 为 None
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/tmp/not-whitelisted.log"}, "user")
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["success"])
        self.assertIn("action_id", result)
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="rejected").first()
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
        # 探测 failed(服务异常) → decide auto(非脚本类无影响面, 测试注入) → restart nginx → 验证 active → verified
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "decide", return_value={
                "decision": "auto", "severity": "low",
                "reasons": ["测试注入: 低危自主执行"], "ai_judgement": None}):
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
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "decide", return_value={
                "decision": "auto", "severity": "low",
                "reasons": ["测试注入: 低危自主执行"], "ai_judgement": None}):
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
        # 探测缓存 85%(低危区间) + decide auto(非脚本类无影响面, 测试注入) → clean_cache → 验证 70% < 80 → verified
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "decide", return_value={
                "decision": "auto", "severity": "low",
                "reasons": ["测试注入: 低危自主执行"], "ai_judgement": None}):
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
        # 磁盘 85%(低危区间), 但 run_cleanup_script(category=system) 中风险 → AI 不可用 fail-closed → 落审批单, 不执行
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
            with _mock_exec([
                    {"success": True, "exit_code": 0,
                     "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                               "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},  # 探测
                    {"success": True, "exit_code": 0,
                     "stdout": "[cleanup][2026-09-08 10:00:00] system: would run: "
                               "journalctl --vacuum-size=200M\n", "stderr": ""},  # dry-run
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
        # category 已非必填: 仅传 mount 即可触发 → 缺省 all(白名单含 docker-prune → 强制审批) → 落审批单
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},  # 探测
                {"success": True, "exit_code": 0,
                 "stdout": "[cleanup][2026-09-08 10:00:00] docker-prune: would run: "
                           "docker image prune -f\n", "stderr": ""},  # dry-run
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


class UnifiedGateTests(_SelfHealTestCase):
    def setUp(self):
        super().setUp()
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system"
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        # 固定冷却时长, 避免受外部 SELFHEAL_COOLDOWN_HOURS 影响导致 flaky
        os.environ["SELFHEAL_COOLDOWN_HOURS"] = "6"
        config.reload_config()

    def tearDown(self):
        os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
        os.environ.pop("SELFHEAL_LOG_PATH_WHITELIST", None)
        os.environ.pop("SELFHEAL_COOLDOWN_HOURS", None)
        config.reload_config()

    def test_high_risk_cleanup_script_hangs_approval_ticket(self):
        # category=all(白名单无 docker-prune → medium → AI 判定缺省 fail-closed → approval)
        # 本用例直接验证: run_cleanup_script 高影响 → 生成 pending 审批单
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
            with _mock_exec([
                    {"success": True, "exit_code": 0,
                     "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                               "/dev/vda1 ext4 50G 45G 5G 90% /\n", "stderr": ""},  # 探测
                    {"success": True, "exit_code": 0,
                     "stdout": "[cleanup][2026-09-08 10:00:00] system: would run: "
                               "journalctl --vacuum-size=200M\n", "stderr": ""},  # dry-run
            ]):
                result = orchestrator.run_selfheal(
                    1, "log_cleanup_script", {"mount": "/", "category": "all"}, "user")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["success"])
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).first()
            self.assertEqual(row.status, "pending")
            self.assertEqual(row.action_name, "run_cleanup_script")

    def test_auto_gate_truncate_executes_when_ai_auto(self):
        # 注入 judge_fn 返回 auto(通过 patch decide 的默认 judge)
        import hermes.selfheal.approval as approval_mod

        def _fake_judge(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
            with _mock_exec([
                    {"success": True, "exit_code": 0,
                     "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                               "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
                    {"success": True, "exit_code": 0, "stdout": "", "stderr": ""},
                    {"success": True, "exit_code": 0,
                     "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                               "/dev/vda1 ext4 50G 30G 20G 60% /\n", "stderr": ""},
            ]):
                result = orchestrator.run_selfheal(
                    1, "disk_clean",
                    {"mount": "/", "path": "/var/log/nginx/access.log"}, "user")
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["success"])

    def test_cooldown_skips_repeat(self):
        from datetime import datetime, timedelta, timezone
        with db.session_scope() as s:
            s.add(models.SelfHealAction(
                server_id=1, scene="disk_clean",
                target=json.dumps({"mount": "/"}),
                severity="low", action_name="truncate_log",
                status="verified", success=True,
                executed_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},
        ]):
            result = orchestrator.run_selfheal(
                1, "disk_clean",
                {"mount": "/", "path": "/var/log/nginx/access.log"}, "user")
        self.assertEqual(result["status"], "noop")
        self.assertIn("冷却", result["reason"])

    def test_cleanup_script_invalid_category_rejected(self):
        # 白名单外 category → build_dry_run_command 抛 ValueError → rejected(与 hard_rule 同语义),
        # 而非逃逸到顶层 catch 变成 failed(避免污染成功率分母)
        with _mock_exec([
                {"success": True, "exit_code": 0,
                 "stdout": "Filesystem Type Size Used Avail Use% Mounted on\n"
                           "/dev/vda1 ext4 50G 42G 8G 85% /\n", "stderr": ""},  # 探测
        ]):
            result = orchestrator.run_selfheal(
                1, "log_cleanup_script",
                {"category": "rm -rf", "mount": "/"}, "user")
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["success"])
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).filter_by(status="rejected").first()
            self.assertIsNotNone(row)
            self.assertIn("不在白名单", row.grade_reasons or "")

    def test_verify_recovered_cleanup_below_safe_water_level_required(self):
        # 清理后 85%: 较基线 92% 有下降, 但仍高于安全水位 DISK_LOW_PCT(80%) → 未恢复
        recovered = orchestrator.verify_recovered(
            "ai_log_cleanup", {"mount": "/"},
            "Filesystem Type Size Used Avail Use% Mounted on\n"
            "/dev/vda1 ext4 50G 42G 8G 85% /\n",
            baseline_pct=92)
        self.assertFalse(recovered)


if __name__ == "__main__":
    unittest.main()
