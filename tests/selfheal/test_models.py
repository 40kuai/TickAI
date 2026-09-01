"""Tests for SelfHealAction model persistence."""
import os
import unittest
from pathlib import Path

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/selfheal.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from hermes.data import db, models  # noqa: E402


class SelfHealActionModelTests(unittest.TestCase):
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

    def test_create_and_read(self):
        with db.session_scope() as s:
            sv = s.query(models.Server).first()
            act = models.SelfHealAction(
                server_id=sv.id, scene="disk_clean", target="/",
                severity="low", action_name="truncate_log",
                rendered_command="truncate -s 0 /var/log/x.log",
                status="executed", triggered_by="user",
                success=True,
                grade_reasons='["磁盘使用率 85% ≥ 低危阈值 80%"]',
            )
            s.add(act)
            s.flush()
            aid = act.id
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, aid)
            self.assertEqual(row.scene, "disk_clean")
            self.assertEqual(row.severity, "low")
            self.assertTrue(row.success)
            self.assertEqual(row.grade_reasons, '["磁盘使用率 85% ≥ 低危阈值 80%"]')

    def test_approval_flow_fields(self):
        with db.session_scope() as s:
            sv = s.query(models.Server).first()
            act = models.SelfHealAction(
                server_id=sv.id, scene="process_restart", target="nginx",
                severity="high", action_name="restart_service",
                rendered_command="systemctl restart nginx",
                status="pending", triggered_by="dialog",
            )
            s.add(act)
            s.flush()
            aid = act.id
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, aid)
            self.assertEqual(row.status, "pending")
            self.assertIsNone(row.approver)

    def test_default_values(self):
        """未显式传 status/triggered_by 时使用模型默认值。"""
        with db.session_scope() as s:
            sv = s.query(models.Server).first()
            act = models.SelfHealAction(
                server_id=sv.id, scene="disk_clean", target="/",
                severity="low", action_name="truncate_log",
                rendered_command="truncate -s 0 /var/log/x.log",
            )
            s.add(act)
            s.flush()
            aid = act.id
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, aid)
            self.assertEqual(row.status, "pending")       # default
            self.assertEqual(row.triggered_by, "user")    # default
            self.assertIsNotNone(row.created_at)

    def test_to_dict_includes_server_name(self):
        with db.session_scope() as s:
            sv = s.query(models.Server).first()
            act = models.SelfHealAction(
                server_id=sv.id, scene="disk_clean", target="/",
                severity="low", action_name="truncate_log",
                rendered_command="truncate -s 0 /var/log/x.log",
                grade_reasons='["reason"]',
            )
            s.add(act)
            s.flush()
            aid = act.id
            d = act.to_dict()  # open session 内 lazy load server relationship
            self.assertEqual(d["server_id"], sv.id)
            self.assertEqual(d["server_name"], "s")
            self.assertEqual(d["scene"], "disk_clean")
            self.assertEqual(d["severity"], "low")
            self.assertEqual(d["status"], "pending")
            self.assertIsNone(d["success"])
            self.assertEqual(d["rendered_command"], "truncate -s 0 /var/log/x.log")
            self.assertEqual(d["grade_reasons"], '["reason"]')
            self.assertIn("id", d)
            # 时间字段为 isoformat 字符串或 None
            self.assertIsInstance(d["created_at"], str)
            self.assertIsNone(d["approved_at"])
            self.assertIsNone(d["executed_at"])

    def test_to_dict_includes_plan_id(self):
        with db.session_scope() as s:
            sv = s.query(models.Server).first()
            act = models.SelfHealAction(
                server_id=sv.id, scene="ai_log_cleanup", target="{}",
                severity="high", action_name="journal_vacuum",
                status="pending", triggered_by="ai", plan_id="plan-1")
            s.add(act)
            s.flush()
            d = act.to_dict()
            self.assertEqual(d["plan_id"], "plan-1")
            s.rollback()


if __name__ == "__main__":
    unittest.main()
