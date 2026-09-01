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
            )
            s.add(act)
            s.flush()
            aid = act.id
        with db.session_scope() as s:
            row = s.get(models.SelfHealAction, aid)
            self.assertEqual(row.scene, "disk_clean")
            self.assertEqual(row.severity, "low")
            self.assertTrue(row.success)

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


if __name__ == "__main__":
    unittest.main()
