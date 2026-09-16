"""Tests for security hardening fixes (#3/#4/#5/#7) + AI operation scope.

Covers:
  - #5 JWT_SECRET unset → random secret + WARN log
  - #7 login sets Secure cookie flag
  - #3 sync config GET response redacts auth_password / api_token
  - #4 tool run endpoint: rejects bare-SSH tools (403), audits visible tools
  - AI operation scope: chat tools payload excludes bare-SSH tools
"""
import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

# Force a temp DB BEFORE importing app modules
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/security_fixes.db")
os.environ["ADMIN_INITIAL_PASSWORD"] = "admin-test-pw"
os.environ["COOKIE_SECURE"] = "true"  # 测试 Secure cookie 标志
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from hermes.data import db, models  # noqa: E402
from hermes.data.models import Base, RunRecord, SyncConfig, User  # noqa: E402
from hermes.tools.registry import registry, is_chat_visible  # noqa: E402


def _wipe_db():
    db.init_db()
    with db.session_scope() as s:
        for table in (RunRecord, SyncConfig, models.UserSession, models.User,
                      models.Server, models.SSHCredential):
            s.query(table).delete()


def _create_admin():
    from hermes.auth import hash_password
    with db.session_scope() as s:
        s.add(User(username="admin", password_hash=hash_password("admin-test-pw"), is_active=True))


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        _wipe_db()
        _create_admin()

    def _client(self):
        from api.main import app
        # Use https base_url so the Secure cookie is actually sent back.
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin-test-pw"})
        assert r.status_code == 200, r.text
        return c

    # ---------------- #7 Secure cookie ----------------
    def test_login_sets_secure_cookie(self):
        from api.main import app
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin-test-pw"})
        self.assertEqual(r.status_code, 200)
        set_cookie = r.headers.get("set-cookie", "")
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)

    # ---------------- #3 sync config redaction ----------------
    def test_sync_config_redacts_secrets(self):
        with db.session_scope() as s:
            s.add(SyncConfig(
                id=1,
                api_url="https://cmdb.example.com/api/hosts",
                auth_type="basic",
                auth_username="svc",
                auth_password="supersecret",
                api_token="super-token",
                response_path="data.list",
            ))
        c = self._client()
        r = c.get("/api/servers/sync/config")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["auth_password"], "***")
        self.assertEqual(d["api_token"], "***")
        self.assertEqual(d["auth_username"], "svc")  # 非敏感字段保持原样

    # ---------------- #4 tool run: reject bare-SSH + audit ----------------
    def test_run_tool_rejects_bare_ssh_tool(self):
        c = self._client()
        r = c.post("/api/tools/check_resources/run", json={"args": {"host": "10.0.0.99", "username": "root", "password": "x"}})
        self.assertEqual(r.status_code, 403)
        # 未被审计
        with db.session_scope() as s:
            self.assertEqual(s.query(RunRecord).count(), 0)

    def test_run_tool_audits_visible_tool(self):
        c = self._client()
        r = c.post("/api/tools/query_runs/run", json={"args": {"limit": 5}})
        self.assertEqual(r.status_code, 200)
        with db.session_scope() as s:
            runs = s.query(RunRecord).all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].command, "query_runs")
            self.assertEqual(runs[0].triggered_by, "user_button")


# ---------------- #5 JWT_SECRET ----------------
class JwtSecretTests(unittest.TestCase):
    def test_jwt_secret_random_when_unset_and_logs_warning(self):
        import logging
        import api.deps as deps
        from hermes.config import settings as settings_mod

        # Patch the source settings.get; reload(deps) re-binds settings_get to
        # the (mocked) value, so the warning + random fallback are exercised.
        with patch.object(settings_mod, "get", return_value=""):
            with patch.object(logging.getLogger("api.deps"), "warning") as mock_warn:
                importlib.reload(deps)
                self.assertTrue(mock_warn.called, "expected a WARN when JWT_SECRET is unset")
                self.assertTrue(deps.JWT_SECRET, "JWT_SECRET should be non-empty")

    def test_jwt_secret_uses_configured_value(self):
        import api.deps as deps
        from hermes.config import settings as settings_mod

        with patch.object(settings_mod, "get", return_value="my-configured-secret"):
            importlib.reload(deps)
            self.assertEqual(deps.JWT_SECRET, "my-configured-secret")


# ---------------- AI operation scope ----------------
class AiOperationScopeTests(unittest.TestCase):
    def test_bare_ssh_tools_not_chat_visible(self):
        # 裸 host/password 的 SSH 工具不可见(LLM 只能通过 server_id 访问)
        self.assertFalse(is_chat_visible("check_resources"))
        self.assertFalse(is_chat_visible("list_services"))

    def test_server_id_wrappers_chat_visible(self):
        self.assertTrue(is_chat_visible("check_resources_on_server"))
        self.assertTrue(is_chat_visible("list_services_on_server"))
        self.assertTrue(is_chat_visible("check_disk_usage"))

    def test_observability_tools_chat_visible(self):
        for name in (
            "list_servers", "query_runs",
            "prometheus_service_health", "nightingale_active_alerts",
            "jenkins_build_records", "ldap_search_user", "run_skill",
            "check_k8s_pods",
        ):
            self.assertTrue(is_chat_visible(name), f"{name} should be chat-visible")

    def test_chat_tools_payload_excludes_bare_ssh(self):
        from hermes.agents import chat as llm_agent
        names = {t["function"]["name"] for t in llm_agent._build_tools_payload()}
        self.assertNotIn("check_resources", names)
        self.assertNotIn("list_services", names)
        self.assertIn("check_resources_on_server", names)


if __name__ == "__main__":
    unittest.main()
