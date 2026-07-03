"""Tests for hermes.tools — query_runs + list_servers."""
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Importing tools has the side-effect of registering query_runs + list_servers
from hermes.tools.ssh import disk as tools  # noqa: F401, E402
# audit removed - was hermes.opslib.audit (legacy)
from hermes.data import db, models  # noqa: E402
from hermes.data.models import RunRecord, Server  # noqa: E402
from hermes.tools.registry import registry  # noqa: E402


def _wipe():
    db.init_db()
    with db.session_scope() as s:
        s.query(RunRecord).delete()
        s.query(Server).delete()


def _add_server(name="web-01", tags="web,prod", password="secret123"):
    with db.session_scope() as s:
        s.add(models.Server(name=name, host="10.0.0.1", username="root",
                            password=password, tags=tags))
    with db.session_scope() as s:
        return s.query(Server).filter_by(name=name).one()


def _add_run(server_id, command="df -Th", status="success", triggered_by="user_button"):
    with db.session_scope() as s:
        s.add(models.RunRecord(
            server_id=server_id, command=command, status=status,
            exit_code=0 if status == "success" else None,
            duration_ms=100, triggered_by=triggered_by,
            stdout="Filesystem...", stderr="", structured_result='{"mounts":[]}',
        ))


class RegistrationTests(unittest.TestCase):
    def test_query_runs_registered(self):
        self.assertTrue(registry.has("query_runs"))
        schema = registry.get("query_runs")["schema"]
        self.assertEqual(schema["name"], "query_runs")

    def test_list_servers_registered(self):
        self.assertTrue(registry.has("list_servers"))


class QueryRunsToolTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.s1 = _add_server("web-01")
        self.s2 = _add_server("db-01")
        _add_run(self.s1.id, "df -Th")
        _add_run(self.s2.id, "df -Th")

    def test_dispatch_returns_json(self):
        out = registry.dispatch("query_runs", {})
        data = json.loads(out)
        self.assertIn("runs", data)
        self.assertIn("count", data)
        self.assertEqual(data["count"], 2)

    def test_filters_by_server_name(self):
        out = registry.dispatch("query_runs", {"server_name": "web-01"})
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["runs"][0]["server_name"], "web-01")

    def test_filters_by_status(self):
        _add_run(self.s1.id, command="bad", status="failed")
        out = registry.dispatch("query_runs", {"status": "failed"})
        data = json.loads(out)
        self.assertEqual(data["count"], 1)

    def test_filters_by_triggered_by(self):
        _add_run(self.s1.id, command="llm", triggered_by="llm_tool_call")
        out = registry.dispatch("query_runs", {"triggered_by": "llm_tool_call"})
        data = json.loads(out)
        self.assertEqual(data["count"], 1)

    def test_since_parameter(self):
        out = registry.dispatch("query_runs", {"since": "1d"})
        data = json.loads(out)
        self.assertGreaterEqual(data["count"], 1)

    def test_limit_parameter(self):
        out = registry.dispatch("query_runs", {"limit": 1})
        data = json.loads(out)
        self.assertEqual(data["count"], 1)

    def test_returns_dict_format(self):
        out = registry.dispatch("query_runs", {})
        data = json.loads(out)
        run = data["runs"][0]
        for key in ("id", "server_id", "server_name", "command", "status",
                    "started_at", "duration_ms", "triggered_by"):
            self.assertIn(key, run, f"missing {key}")


class ListServersToolTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        _add_server("web-01", tags="web,prod")
        _add_server("db-01", tags="db,prod")

    def test_dispatch_returns_json(self):
        out = registry.dispatch("list_servers", {})
        data = json.loads(out)
        self.assertIn("servers", data)
        self.assertEqual(data["count"], 2)

    def test_never_returns_password(self):
        """Critical security test: LLM must never see passwords."""
        out = registry.dispatch("list_servers", {})
        data = json.loads(out)
        for sv in data["servers"]:
            self.assertNotEqual(sv["password"], "secret123")
            self.assertEqual(sv["password"], "***")

    def test_filters_by_tag(self):
        out = registry.dispatch("list_servers", {"tag": "web"})
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["servers"][0]["name"], "web-01")

    def test_excludes_inactive_servers(self):
        with db.session_scope() as s:
            sv = s.query(Server).filter_by(name="web-01").one()
            sv.is_active = False
        out = registry.dispatch("list_servers", {})
        data = json.loads(out)
        names = {sv["name"] for sv in data["servers"]}
        self.assertNotIn("web-01", names)


class CheckFnTests(unittest.TestCase):
    def test_tools_requirements_satisfied(self):
        self.assertTrue(tools.check_ops_tools_requirements())


if __name__ == "__main__":
    unittest.main()
