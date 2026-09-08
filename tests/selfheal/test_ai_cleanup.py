"""Tests for hermes.selfheal.ai_cleanup — AI 清理策略校验与审批单生成(通道二)."""
import os
import unittest

from hermes.data import db, models  # noqa: E402
from hermes.selfheal import ai_cleanup, config  # noqa: E402


class _AiCleanupTestCase(unittest.TestCase):
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
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log"
        os.environ["SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST"] = "system,service,docker-log,docker-prune"
        config.reload_config()


class AiCleanupValidateTests(_AiCleanupTestCase):
    def test_validate_truncate_file_allowed(self):
        ok, item, reason = ai_cleanup._validate_item(
            {"type": "truncate_file", "path": "/var/log/nginx/access.log"})
        self.assertTrue(ok)
        self.assertEqual(item["action"], "truncate_log")
        self.assertEqual(item["target"]["path"], "/var/log/nginx/access.log")

    def test_validate_truncate_file_rejects_outside_whitelist(self):
        ok, _, reason = ai_cleanup._validate_item(
            {"type": "truncate_file", "path": "/etc/passwd"})
        self.assertFalse(ok)
        self.assertIn("白名单", reason)

    def test_validate_journal_vacuum_allowed(self):
        ok, item, _ = ai_cleanup._validate_item({"type": "journal_vacuum", "size": "200"})
        self.assertTrue(ok)
        self.assertEqual(item["action"], "journal_vacuum")

    def test_validate_docker_log_truncate_allowed(self):
        p = "/var/lib/docker/containers/abc/abc-json.log"
        ok, item, _ = ai_cleanup._validate_item({"type": "docker_log_truncate", "path": p})
        self.assertTrue(ok)
        self.assertEqual(item["action"], "docker_log_truncate")

    def test_validate_run_cleanup_category_allowed(self):
        ok, item, _ = ai_cleanup._validate_item({"type": "run_cleanup_category", "category": "system"})
        self.assertTrue(ok)
        self.assertEqual(item["action"], "run_cleanup_script")

    def test_validate_unknown_type_rejected(self):
        ok, _, reason = ai_cleanup._validate_item({"type": "rm -rf /"})
        self.assertFalse(ok)
        self.assertIn("未知类型", reason)


class AiCleanupPlanTests(_AiCleanupTestCase):
    def test_create_plan_generates_pending_actions(self):
        # AI 判定不可用(fail-closed) → approval → 全部挂审批单, 不执行
        import hermes.selfheal.approval as approval_mod
        from unittest.mock import patch
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
            strategy = {"items": [
                {"type": "journal_vacuum", "size": "200"},
                {"type": "truncate_file", "path": "/var/log/nginx/access.log"},
                {"type": "truncate_file", "path": "/etc/passwd"},  # 非法项
            ]}
            result = ai_cleanup.create_plan(1, strategy, plan_id="plan-1")
        self.assertEqual(result["accepted"], 2)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["auto"], 0)
        with db.session_scope() as s:
            rows = s.query(models.SelfHealAction).filter(
                models.SelfHealAction.plan_id == "plan-1").all()
            self.assertEqual(len(rows), 2)
            for r in rows:
                self.assertEqual(r.status, "pending")
                self.assertEqual(r.triggered_by, "ai")
                self.assertEqual(r.severity, "high")

    def test_create_plan_items_not_list_rejected(self):
        result = ai_cleanup.create_plan(1, {"mount": "/", "items": "oops"}, plan_id="plan-x")
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["auto"], 0)
        self.assertEqual(result["items"][0]["ok"], False)

    def test_create_plan_item_not_dict_rejected(self):
        result = ai_cleanup.create_plan(1, {"mount": "/", "items": ["rm -rf /", 42]}, plan_id="plan-y")
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["rejected"], 2)
        self.assertEqual(result["auto"], 0)


class UnifiedGatePlanTests(_AiCleanupTestCase):
    """AI 策略接入统一出口: approval 挂单 / reject 拒绝 / auto 直接执行。"""

    def tearDown(self):
        os.environ.pop("SELFHEAL_LOG_PATH_WHITELIST", None)
        os.environ.pop("SELFHEAL_LOG_CLEANUP_CATEGORIES_WHITELIST", None)
        config.reload_config()

    def test_approval_item_creates_pending(self):
        # AI 判定不可用(fail-closed) → approval → 挂审批单 pending, grade_reasons 含 AI 建议
        import hermes.selfheal.approval as approval_mod
        from unittest.mock import patch
        with patch.object(approval_mod, "_default_judge",
                          side_effect=RuntimeError("no TOKENHUB_API_KEY in test")):
            strategy = {"mount": "/", "items": [
                {"type": "truncate_file", "path": "/var/log/nginx/access.log",
                 "size_mb": 4800},
            ]}
            result = ai_cleanup.create_plan(1, strategy, plan_id="p-test-1")
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["auto"], 0)
        self.assertEqual(result["items"][0]["status"], "pending")
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).first()
            self.assertEqual(row.status, "pending")
            self.assertIn("AI", row.grade_reasons or "")

    def test_reject_item_not_persisted(self):
        # 未知类型 → _validate_item 拒绝 → 不生成审批单
        strategy = {"mount": "/", "items": [
            {"type": "delete_everything", "path": "/"},
        ]}
        result = ai_cleanup.create_plan(1, strategy, plan_id="p-test-2")
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["auto"], 0)
        with db.session_scope() as s:
            self.assertEqual(s.query(models.SelfHealAction).count(), 0)

    def test_auto_item_executes_immediately(self):
        # AI 判定 auto(score<=2) + 影响面非空 → 统一出口放行 → 直接执行+验证
        # exec_ssh 序列: 基线探测(92%) → 执行(truncate) → 验证探测(70% < 80%) → verified
        import hermes.selfheal.approval as approval_mod
        from unittest.mock import patch

        def _fake_judge(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

        df = ("Filesystem Type Size Used Avail Use% Mounted on\n"
              "/dev/vda1 ext4 50G 46G 4G 92% /\n")
        df_ok = ("Filesystem Type Size Used Avail Use% Mounted on\n"
                 "/dev/vda1 ext4 50G 35G 15G 70% /\n")
        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
            with patch("hermes.selfheal.ai_cleanup.actions.exec_ssh",
                       side_effect=[
                           {"success": True, "stdout": df, "stderr": "", "exit_code": 0},
                           {"success": True, "stdout": "", "stderr": "", "exit_code": 0},
                           {"success": True, "stdout": df_ok, "stderr": "", "exit_code": 0},
                       ]):
                strategy = {"mount": "/", "items": [
                    {"type": "truncate_file", "path": "/var/log/nginx/access.log",
                     "size_mb": 2},
                ]}
                result = ai_cleanup.create_plan(1, strategy, plan_id="p-test-3")
        # auto → 直接 execute_and_verify(SSH 执行被 mock; 验证探测 70% → verified)
        self.assertEqual(result["auto"], 1)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["items"][0]["status"], "verified")
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).first()
            self.assertEqual(row.status, "verified")
            self.assertTrue(row.success)


if __name__ == "__main__":
    unittest.main()
