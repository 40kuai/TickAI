"""Tests for hermes.db + models."""
import os
import unittest
from pathlib import Path

# Force a temp DB BEFORE importing opslib modules
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from hermes.data import db, models  # noqa: E402
from hermes.data.models import Base, Server, RunRecord, Conversation  # noqa: E402


def _wipe_db():
    """Truncate all tables. Used by setUp to isolate tests.
    Calls init_db() first to ensure tables exist."""
    db.init_db()
    with db.session_scope() as s:
        s.query(models.RunRecord).delete()
        s.query(models.Conversation).delete()
        s.query(models.Server).delete()
        s.query(models.SSHCredential).delete()


def _add_server(name: str, host: str = "10.0.0.1", password: str = "x"):
    """Add a Server with a bound default SSH credential."""
    with db.session_scope() as s:
        cred = models.SSHCredential(
            name=f"{name}-cred", username="root", password=password, is_default=True,
        )
        s.add(cred)
        s.flush()
        s.add(models.Server(name=name, host=host, ssh_credential_id=cred.id))
    with db.session_scope() as s:
        return s.query(Server).filter_by(name=name).one()


class InitDbTests(unittest.TestCase):
    def setUp(self):
        _wipe_db()
        db.init_db()

    def test_creates_all_tables(self):
        # Tables exist if queries don't raise
        with db.session_scope() as s:
            self.assertEqual(s.query(Server).count(), 0)
            self.assertEqual(s.query(RunRecord).count(), 0)
            self.assertEqual(s.query(Conversation).count(), 0)

    def test_init_is_idempotent(self):
        db.init_db()  # calling twice should not raise
        db.init_db()
        with db.session_scope() as s:
            self.assertEqual(s.query(Server).count(), 0)

    def test_db_file_is_created(self):
        self.assertTrue(Path(db.DB_PATH).exists())

    def test_db_file_chmod_600(self):
        stat = os.stat(db.DB_PATH)
        perms = stat.st_mode & 0o777
        self.assertEqual(perms, 0o600, f"expected 0o600, got {oct(perms)}")


class ServerModelTests(unittest.TestCase):
    def setUp(self):
        _wipe_db()

    def test_create_and_retrieve(self):
        with db.session_scope() as s:
            s.add(models.Server(name="web-01", host="10.0.0.1"))
        with db.session_scope() as s:
            srv = s.query(Server).filter_by(name="web-01").one()
            self.assertEqual(srv.host, "10.0.0.1")
            self.assertTrue(srv.is_active)
            self.assertIsNotNone(srv.created_at)

    def test_name_unique_constraint(self):
        with db.session_scope() as s:
            s.add(models.Server(name="dup", host="a"))
        with self.assertRaises(Exception):
            with db.session_scope() as s:
                s.add(models.Server(name="dup", host="b"))

    def test_to_dict_never_exposes_password(self):
        """Server.to_dict must not contain any password field (creds live on SSHCredential)."""
        with db.session_scope() as s:
            cred = models.SSHCredential(name="c", username="r", password="secret")
            s.add(cred)
            s.flush()
            s.add(models.Server(name="redact-test", host="a", ssh_credential_id=cred.id))
        with db.session_scope() as s:
            srv = s.query(Server).filter_by(name="redact-test").one()
            d = srv.to_dict()
            self.assertNotIn("password", d)
            self.assertEqual(d["ssh_credential_name"], "c")

    def test_ssh_credential_to_dict_redacts_password(self):
        with db.session_scope() as s:
            s.add(models.SSHCredential(name="c2", username="r", password="secret"))
        with db.session_scope() as s:
            cred = s.query(models.SSHCredential).filter_by(name="c2").one()
            d = cred.to_dict(redact_password=True)
            self.assertEqual(d["password"], "***")
            d2 = cred.to_dict(redact_password=False)
            self.assertEqual(d2["password"], "secret")

    def test_soft_delete_via_is_active(self):
        with db.session_scope() as s:
            s.add(models.Server(name="sd", host="a"))
        with db.session_scope() as s:
            srv = s.query(Server).filter_by(name="sd").one()
            srv.is_active = False
        with db.session_scope() as s:
            self.assertEqual(
                s.query(Server).filter(Server.is_active == True).count(), 0  # noqa: E712
            )


class RunRecordModelTests(unittest.TestCase):
    def setUp(self):
        _wipe_db()
        _add_server("rr")

    def test_create_run(self):
        with db.session_scope() as s:
            srv = s.query(Server).filter_by(name="rr").one()
            s.add(models.RunRecord(
                server_id=srv.id, command="df -Th", status="success",
                exit_code=0, duration_ms=1200,
            ))
        with db.session_scope() as s:
            run = s.query(RunRecord).first()
            self.assertEqual(run.command, "df -Th")
            self.assertEqual(run.status, "success")
            self.assertEqual(run.triggered_by, "user_button")  # default

    def test_to_dict_includes_server_name(self):
        with db.session_scope() as s:
            srv = s.query(Server).filter_by(name="rr").one()
            s.add(models.RunRecord(server_id=srv.id, command="df", status="success",
                                   exit_code=0, duration_ms=100))
        with db.session_scope() as s:
            run = s.query(RunRecord).first()
            d = run.to_dict()
            self.assertEqual(d["server_name"], "rr")
            self.assertIn("started_at", d)

    def test_structured_result_stored_as_json_string(self):
        import json
        with db.session_scope() as s:
            srv = s.query(Server).filter_by(name="rr").one()
            s.add(models.RunRecord(
                server_id=srv.id, command="df -Th", status="success",
                exit_code=0, duration_ms=100,
                structured_result=json.dumps({"mounts": [], "summary": {}}),
            ))
        with db.session_scope() as s:
            run = s.query(RunRecord).first()
            self.assertIn("mounts", run.structured_result)


class ConversationModelTests(unittest.TestCase):
    def setUp(self):
        _wipe_db()

    def test_create_conversation(self):
        with db.session_scope() as s:
            s.add(models.Conversation(title="test", messages_json='[{"role":"user","content":"hi"}]'))
        with db.session_scope() as s:
            conv = s.query(Conversation).first()
            self.assertEqual(conv.title, "test")
            self.assertEqual(conv.total_runs, 0)


class SessionScopeTests(unittest.TestCase):
    def setUp(self):
        _wipe_db()

    def test_commit_on_success(self):
        with db.session_scope() as s:
            s.add(models.Server(name="scope1", host="a"))
        with db.session_scope() as s:
            self.assertEqual(s.query(Server).filter_by(name="scope1").count(), 1)

    def test_rollback_on_exception(self):
        with self.assertRaises(RuntimeError):
            with db.session_scope() as s:
                s.add(models.Server(name="scope2", host="a"))
                raise RuntimeError("boom")
        with db.session_scope() as s:
            self.assertEqual(s.query(Server).filter_by(name="scope2").count(), 0)


if __name__ == "__main__":
    unittest.main()
