"""Tests for hermes.tools.selfheal — the run_selfheal dialog tool (KR2 Task 8).

覆盖:
- run_selfheal 已注册且 schema 形状正确(server_id integer / scene enum / target object)
- handler 缺 scene / server_id 非 int / scene 非法 → tool_error
- handler 调用 orchestrator.run_selfheal(triggered_by="dialog") 并剔除 rendered_command
- is_chat_visible("run_selfheal") 为 True
"""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

# 测试 DB 隔离:必须先于任何 hermes/api 模块导入设置环境变量
os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/selfheal_tool.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# import hermes.tools 会触发 auto_register_tools 自动注册(run_selfheal 由
# hermes/tools/selfheal/tools.py 注册);再显式 import 目标模块保证确定性。
import hermes.tools  # noqa: F401, E402
from hermes.tools.registry import is_chat_visible, registry  # noqa: E402
from hermes.tools.selfheal import tools as selfheal_tools  # noqa: E402

_VALID_TARGET = {"mount": "/", "path": "/var/log/nginx/access.log"}


class SchemaTests(unittest.TestCase):
    def test_schema_registered(self):
        schema = registry.get("run_selfheal")["schema"]
        self.assertEqual(schema["name"], "run_selfheal")
        self.assertEqual(
            schema["parameters"]["required"], ["server_id", "scene", "target"]
        )

    def test_schema_parameters(self):
        schema = registry.get("run_selfheal")["schema"]
        props = schema["parameters"]["properties"]
        self.assertEqual(props["server_id"]["type"], "integer")
        self.assertEqual(
            props["scene"]["enum"],
            ["process_restart", "disk_clean", "cache_clean"],
        )
        self.assertEqual(props["target"]["type"], "object")


class HandlerValidationTests(unittest.TestCase):
    def test_missing_server_id_returns_error(self):
        out = selfheal_tools.run_selfheal_handler(
            {"scene": "disk_clean", "target": _VALID_TARGET}
        )
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_missing_scene_returns_error(self):
        out = selfheal_tools.run_selfheal_handler(
            {"server_id": 1, "target": _VALID_TARGET}
        )
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_missing_target_returns_error(self):
        out = selfheal_tools.run_selfheal_handler(
            {"server_id": 1, "scene": "disk_clean"}
        )
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_invalid_server_id_returns_error(self):
        out = selfheal_tools.run_selfheal_handler(
            {"server_id": "abc", "scene": "disk_clean", "target": _VALID_TARGET}
        )
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_invalid_scene_returns_error(self):
        out = selfheal_tools.run_selfheal_handler(
            {"server_id": 1, "scene": "reboot", "target": {}}
        )
        payload = json.loads(out)
        self.assertIn("error", payload)


class HandlerOrchestratorTests(unittest.TestCase):
    def test_calls_orchestrator_and_strips_rendered_command(self):
        fake = {
            "severity": "low",
            "status": "verified",
            "success": True,
            "action_id": 42,
            "action_name": "restart_service",
            "reasons": [],
            "rendered_command": "systemctl restart nginx",
        }
        with patch("hermes.selfheal.orchestrator.run_selfheal", return_value=fake) as m:
            out = selfheal_tools.run_selfheal_handler(
                {"server_id": 7, "scene": "process_restart",
                 "target": {"service": "nginx"}}
            )
        m.assert_called_once_with(
            server_id=7,
            scene="process_restart",
            target={"service": "nginx"},
            triggered_by="dialog",
        )
        payload = json.loads(out)
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["action_name"], "restart_service")
        self.assertNotIn("rendered_command", payload)

    def test_orchestrator_exception_returns_error(self):
        with patch("hermes.selfheal.orchestrator.run_selfheal",
                   side_effect=RuntimeError("boom")):
            out = selfheal_tools.run_selfheal_handler(
                {"server_id": 1, "scene": "disk_clean", "target": _VALID_TARGET}
            )
        payload = json.loads(out)
        self.assertIn("error", payload)
        self.assertIn("boom", payload["error"])


class VisibilityTests(unittest.TestCase):
    def test_chat_visible(self):
        self.assertTrue(is_chat_visible("run_selfheal"))


if __name__ == "__main__":
    unittest.main()
