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
        strategy = {"items": [
            {"type": "journal_vacuum", "size": "200"},
            {"type": "truncate_file", "path": "/var/log/nginx/access.log"},
            {"type": "truncate_file", "path": "/etc/passwd"},  # 非法项
        ]}
        result = ai_cleanup.create_plan(1, strategy, plan_id="plan-1")
        self.assertEqual(result["accepted"], 2)
        self.assertEqual(result["rejected"], 1)
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
        self.assertEqual(result["items"][0]["ok"], False)

    def test_create_plan_item_not_dict_rejected(self):
        result = ai_cleanup.create_plan(1, {"mount": "/", "items": ["rm -rf /", 42]}, plan_id="plan-y")
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["rejected"], 2)


if __name__ == "__main__":
    unittest.main()
