"""Tests for hermes.selfheal.ai_cleanup — AI 清理策略校验与审批单生成(通道二)."""
import json
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

    def test_auto_respects_cooldown_degrades_to_approval(self):
        # 冷却期内同 server+scene+action 已有 verified 记录 → auto 降级为 approval 挂单
        from datetime import datetime, timedelta, timezone
        import hermes.selfheal.approval as approval_mod
        from unittest.mock import patch

        def _fake_judge(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

        with db.session_scope() as s:
            s.add(models.SelfHealAction(
                server_id=1, scene="ai_log_cleanup",
                target=json.dumps({"mount": "/", "path": "/var/log/nginx/access.log"}),
                severity="low", action_name="truncate_log",
                status="verified", success=True,
                executed_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
            strategy = {"mount": "/", "items": [
                {"type": "truncate_file", "path": "/var/log/nginx/access.log",
                 "size_mb": 2},
            ]}
            result = ai_cleanup.create_plan(1, strategy, plan_id="p-cool-1")
        self.assertEqual(result["auto"], 0)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["items"][0]["status"], "pending")
        with db.session_scope() as s:
            rows = s.query(models.SelfHealAction).all()
            # 预置 verified + 新挂单 pending 各一条
            self.assertEqual(len(rows), 2)
            pendings = [r for r in rows if r.status == "pending"]
            self.assertEqual(len(pendings), 1)

    def test_invalid_size_mb_rejected(self):
        # size_mb 非有限数/布尔 → 该项拒绝, 不逃逸 create_plan
        import hermes.selfheal.approval as approval_mod
        from unittest.mock import patch
        for bad in ["abc", "nan", "inf", True]:
            with patch.object(approval_mod, "_default_judge",
                              side_effect=RuntimeError("no key in test")):
                strategy = {"mount": "/", "items": [
                    {"type": "truncate_file", "path": "/var/log/nginx/access.log",
                     "size_mb": bad},
                ]}
                result = ai_cleanup.create_plan(1, strategy, plan_id=f"p-bad-{bad}")
            self.assertEqual(result["rejected"], 1, msg=f"size_mb={bad!r}")
            self.assertEqual(result["accepted"], 0, msg=f"size_mb={bad!r}")
            self.assertIn("size_mb", result["items"][0]["reason"],
                          msg=f"size_mb={bad!r}")

    def test_auto_exec_failure_marks_failed(self):
        # AI auto → 执行失败(exec_ssh success=False) → status=failed 落库, 不抛 400
        import hermes.selfheal.approval as approval_mod
        from unittest.mock import patch

        def _fake_judge(payload):
            return {"risk_score": 1, "recommendation": "auto", "reasons": ["影响极小"]}

        df = ("Filesystem Type Size Used Avail Use% Mounted on\n"
              "/dev/vda1 ext4 50G 46G 4G 92% /\n")
        with patch.object(approval_mod, "_default_judge", side_effect=_fake_judge):
            with patch("hermes.selfheal.ai_cleanup.actions.exec_ssh",
                       side_effect=[
                           {"success": True, "stdout": df, "stderr": "", "exit_code": 0},
                           {"success": False, "error": "SSH error: command failed",
                            "stdout": "", "stderr": "permission denied", "exit_code": 1},
                       ]):
                strategy = {"mount": "/", "items": [
                    {"type": "truncate_file", "path": "/var/log/nginx/access.log",
                     "size_mb": 2},
                ]}
                result = ai_cleanup.create_plan(1, strategy, plan_id="p-exec-fail-1")
        self.assertEqual(result["auto"], 1)
        self.assertEqual(result["items"][0]["status"], "failed")
        with db.session_scope() as s:
            row = s.query(models.SelfHealAction).first()
            self.assertEqual(row.status, "failed")
            self.assertFalse(row.success)

    def test_unknown_decision_fails_closed(self):
        # decide 返回未知决策值(防御) → fail-closed 挂审批单, 绝不直接执行
        from unittest.mock import patch
        import hermes.selfheal.approval as approval_mod
        with patch.object(approval_mod, "decide", return_value={
                "decision": "maybe", "severity": "high",
                "reasons": ["未知决策"], "ai_judgement": None}):
            strategy = {"mount": "/", "items": [
                {"type": "truncate_file", "path": "/var/log/nginx/access.log",
                 "size_mb": 2},
            ]}
            result = ai_cleanup.create_plan(1, strategy, plan_id="p-unk-1")
        self.assertEqual(result["auto"], 0)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["items"][0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
