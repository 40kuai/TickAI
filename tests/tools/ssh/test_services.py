"""Tests for hermes.system_tools — LLM-facing wrappers for system tools."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Importing the module has the side-effect of registering the new tools.
from hermes.tools.ssh.services import system_tools  # noqa: F401, E402
from hermes.data import db, models  # noqa: E402
from hermes.data.models import RunRecord, Server  # noqa: E402
from hermes.tools.registry import registry  # noqa: E402


def _wipe():
    db.init_db()
    with db.session_scope() as s:
        s.query(RunRecord).delete()
        s.query(Server).delete()


def _add_server(name="web-01", password="secret123"):
    with db.session_scope() as s:
        s.add(models.Server(name=name, host="10.0.0.1", username="root",
                            password=password, tags="web,prod"))
    with db.session_scope() as s:
        return s.query(Server).filter_by(name=name).one()


def _fake_resources_result():
    return json.dumps({
        "load_1_5_15": [0.42, 0.38, 0.41],
        "load_per_core_1": 0.10,
        "cpu_cores": 4,
        "memory": {"total_mb": 16000, "used_mb": 12000, "avail_mb": 4000,
                   "use_pct": 75, "swap_total_mb": 2048, "swap_used_mb": 100,
                   "swap_use_pct": 5},
        "top_processes": [{"pid": 1234, "user": "mysql", "cpu_pct": 42.0,
                           "mem_pct": 15.0, "command": "mysqld"}],
        "pressure_level": "low",
        "pressure_reasons": ["all metrics within healthy range"],
    })


def _fake_services_result():
    return json.dumps({
        "total": 3,
        "abnormal": 1,
        "services": [
            {"name": "nginx", "state": "active", "sub_state": "running",
             "is_abnormal": False, "description": "web server"},
            {"name": "myapp", "state": "failed", "sub_state": "dead",
             "is_abnormal": True, "description": "MyApp"},
        ],
    })


class RegistrationTests(unittest.TestCase):
    def test_check_resources_on_server_registered(self):
        self.assertTrue(registry.has("check_resources_on_server"))
        schema = registry.get("check_resources_on_server")["schema"]
        self.assertEqual(schema["name"], "check_resources_on_server")

    def test_list_services_on_server_registered(self):
        self.assertTrue(registry.has("list_services_on_server"))


class CheckResourcesToolTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.server = _add_server("web-01")

    def test_rejects_missing_server_id(self):
        out = registry.dispatch("check_resources_on_server", {})
        data = json.loads(out)
        self.assertIn("error", data)

    def test_rejects_unknown_server(self):
        out = registry.dispatch("check_resources_on_server", {"server_id": 9999})
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertIn("not found", data["error"])

    def test_calls_ssh_and_returns_result(self):
        with patch("hermes.system_tools.check_resources_handler",
                    return_value=_fake_resources_result()):
            out = registry.dispatch("check_resources_on_server",
                                     {"server_id": self.server.id})
        data = json.loads(out)
        self.assertEqual(data["server_name"], "web-01")
        self.assertEqual(data["load_1_5_15"], [0.42, 0.38, 0.41])
        self.assertEqual(data["pressure_level"], "low")

    def test_does_not_expose_password(self):
        with patch("hermes.system_tools.check_resources_handler",
                    return_value=_fake_resources_result()):
            out = registry.dispatch("check_resources_on_server",
                                     {"server_id": self.server.id})
        data = json.loads(out)
        # password field must not appear anywhere in the response
        self.assertNotIn("password", json.dumps(data))

    def test_persists_audit_with_llm_source(self):
        with patch("hermes.system_tools.check_resources_handler",
                    return_value=_fake_resources_result()):
            registry.dispatch("check_resources_on_server",
                               {"server_id": self.server.id})
        with db.session_scope() as s:
            runs = s.query(RunRecord).all()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].command, "check_resources")
        self.assertEqual(runs[0].triggered_by, "llm_tool_call")
        self.assertIn("tool_name", runs[0].triggered_context)
        self.assertIn("check_resources_on_server", runs[0].triggered_context)

    def test_ssh_failure_persists_ssh_error(self):
        with patch("hermes.system_tools.check_resources_handler",
                    return_value=json.dumps({"error": "SSH error: Connection refused"})):
            out = registry.dispatch("check_resources_on_server",
                                     {"server_id": self.server.id})
        data = json.loads(out)
        # Even on SSH error, we return a clean result envelope
        self.assertIn("error", data)
        with db.session_scope() as s:
            runs = s.query(RunRecord).all()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, "ssh_error")


class ListServicesToolTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.server = _add_server("web-01")

    def test_rejects_missing_server_id(self):
        out = registry.dispatch("list_services_on_server", {})
        data = json.loads(out)
        self.assertIn("error", data)

    def test_calls_ssh_and_returns_result(self):
        with patch("hermes.system_tools.list_services_handler",
                    return_value=_fake_services_result()):
            out = registry.dispatch("list_services_on_server",
                                     {"server_id": self.server.id})
        data = json.loads(out)
        self.assertEqual(data["server_name"], "web-01")
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["abnormal"], 1)
        self.assertEqual(len(data["services"]), 2)
        # abnormal one
        abnormal = [s for s in data["services"] if s["is_abnormal"]]
        self.assertEqual(len(abnormal), 1)
        self.assertEqual(abnormal[0]["name"], "myapp")

    def test_does_not_expose_password(self):
        with patch("hermes.system_tools.list_services_handler",
                    return_value=_fake_services_result()):
            out = registry.dispatch("list_services_on_server",
                                     {"server_id": self.server.id})
        data = json.loads(out)
        self.assertNotIn("password", json.dumps(data))

    def test_persists_audit_with_llm_source(self):
        with patch("hermes.system_tools.list_services_handler",
                    return_value=_fake_services_result()):
            registry.dispatch("list_services_on_server",
                               {"server_id": self.server.id})
        with db.session_scope() as s:
            runs = s.query(RunRecord).all()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].command, "list_services")
        self.assertEqual(runs[0].triggered_by, "llm_tool_call")

    def test_ssh_failure_persists_ssh_error(self):
        with patch("hermes.system_tools.list_services_handler",
                    return_value=json.dumps({"error": "SSH error: timeout"})):
            out = registry.dispatch("list_services_on_server",
                                     {"server_id": self.server.id})
        data = json.loads(out)
        self.assertIn("error", data)
        with db.session_scope() as s:
            runs = s.query(RunRecord).all()
        self.assertEqual(runs[0].status, "ssh_error")


if __name__ == "__main__":
    unittest.main()
