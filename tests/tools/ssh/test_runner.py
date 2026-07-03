"""Tests for hermes.ssh_runner."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from hermes.data import db, models  # noqa: E402
from hermes.tools.ssh import runner as ssh_runner  # noqa: E402
from hermes.data.models import RunRecord, Server  # noqa: E402


def _wipe():
    db.init_db()
    with db.session_scope() as s:
        s.query(RunRecord).delete()
        s.query(Server).delete()


def _add_server(name="web-01"):
    with db.session_scope() as s:
        s.add(models.Server(name=name, host="10.0.0.1", username="root", password="x"))
    with db.session_scope() as s:
        return s.query(Server).filter_by(name=name).one()


def _mock_handler(return_value: str):
    """Return a context manager that patches check_disk_handler."""
    return patch("hermes.ssh_runner.check_disk_handler",
                 return_value=return_value)


class SuccessfulRunTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.server = _add_server()

    def test_persists_run_record(self):
        result = json.dumps({"mounts": [{"use_pct": 50}], "summary": {"total_mounts": 1}})
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "df -Th", "user_button")

        self.assertIsNotNone(run.id)
        self.assertEqual(run.command, "df -Th")
        self.assertEqual(run.status, "success")
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(run.triggered_by, "user_button")
        self.assertGreaterEqual(run.duration_ms, 0)
        # And it was committed to DB
        with db.session_scope() as s:
            count = s.query(RunRecord).filter_by(server_id=self.server.id).count()
            self.assertEqual(count, 1)

    def test_stores_structured_result(self):
        result = json.dumps({"mounts": [], "summary": {"total_mounts": 0, "warning_count": 0, "critical_count": 0}})
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "df -Th", "user_button")
        self.assertIsNotNone(run.structured_result)
        self.assertIn("mounts", run.structured_result)

    def test_updates_server_last_seen_on_success(self):
        result = json.dumps({"mounts": [], "summary": {}})
        with _mock_handler(result):
            ssh_runner.run_command(self.server.id, "df -Th", "user_button")
        with db.session_scope() as s:
            srv = s.get(Server, self.server.id)
            self.assertIsNotNone(srv.last_seen_at)

    def test_does_not_update_last_seen_on_failure(self):
        result = json.dumps({"error": "SSH error: ConnectionError: refused"})
        with _mock_handler(result):
            ssh_runner.run_command(self.server.id, "df -Th", "user_button")
        with db.session_scope() as s:
            srv = s.get(Server, self.server.id)
            self.assertIsNone(srv.last_seen_at)


class FailureRunTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.server = _add_server()

    def test_ssh_error_persisted(self):
        result = json.dumps({"error": "SSH error: ConnectionError: connection refused"})
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "df -Th", "user_button")
        self.assertEqual(run.status, "ssh_error")
        self.assertIn("connection refused", run.stderr)
        self.assertIsNone(run.exit_code)

    def test_command_validation_error_persisted(self):
        result = json.dumps({"error": "command rejected: command not allowed: 'rm -rf /'"})
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "rm -rf /", "user_button")
        self.assertEqual(run.status, "failed")
        self.assertIn("not allowed", run.stderr)


class ErrorTests(unittest.TestCase):
    def setUp(self):
        _wipe()

    def test_server_not_found_raises(self):
        with self.assertRaises(ValueError):
            ssh_runner.run_command(9999, "df -Th", "user_button")


class ContextTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.server = _add_server()

    def test_triggered_by_recorded(self):
        result = json.dumps({"mounts": [], "summary": {}})
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "df -Th", "llm_tool_call")
        self.assertEqual(run.triggered_by, "llm_tool_call")

    def test_triggered_context_json_encoded(self):
        result = json.dumps({"mounts": [], "summary": {}})
        ctx = {"tool_call_id": "call_abc", "conversation_id": 5}
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "df -Th", "llm_tool_call",
                                        triggered_context=ctx)
        self.assertIsNotNone(run.triggered_context)
        decoded = json.loads(run.triggered_context)
        self.assertEqual(decoded["tool_call_id"], "call_abc")
        self.assertEqual(decoded["conversation_id"], 5)

    def test_triggered_context_optional(self):
        result = json.dumps({"mounts": [], "summary": {}})
        with _mock_handler(result):
            run = ssh_runner.run_command(self.server.id, "df -Th", "user_button")
        self.assertIsNone(run.triggered_context)


class CheckDiskHandlerIntegrationTests(unittest.TestCase):
    """Verify the actual integration: ssh_runner passes server creds to check_disk_handler."""

    def setUp(self):
        _wipe()
        self.server = _add_server()

    def test_passes_correct_args_to_check_disk_handler(self):
        captured = {}

        def fake_handler(args, **kwargs):
            captured.update(args)
            return json.dumps({"mounts": [], "summary": {}})

        with patch("hermes.ssh_runner.check_disk_handler", side_effect=fake_handler):
            ssh_runner.run_command(self.server.id, "df -Th", "user_button")

        self.assertEqual(captured["host"], "10.0.0.1")
        self.assertEqual(captured["username"], "root")
        self.assertEqual(captured["password"], "x")
        self.assertEqual(captured["port"], 22)
        self.assertEqual(captured["command"], "df -Th")


if __name__ == "__main__":
    unittest.main()
