"""Tests for tools.registry — tool_result / tool_error / ToolRegistry."""
import json
import unittest

from hermes.tools.registry import ToolRegistry, registry, tool_error, tool_result


class ToolResultTests(unittest.TestCase):
    def test_returns_json_string(self):
        out = tool_result(rolls=[1, 2, 3], total=6)
        self.assertIsInstance(out, str)
        data = json.loads(out)
        self.assertEqual(data, {"rolls": [1, 2, 3], "total": 6})

    def test_empty_payload_is_valid_json_object(self):
        out = tool_result()
        self.assertEqual(json.loads(out), {})

    def test_chinese_keys_preserved(self):
        out = tool_result(结果=42)
        self.assertEqual(json.loads(out), {"结果": 42})


class ToolErrorTests(unittest.TestCase):
    def test_returns_error_envelope(self):
        out = tool_error("something went wrong")
        self.assertIsInstance(out, str)
        data = json.loads(out)
        self.assertEqual(data, {"error": "something went wrong"})

    def test_error_does_not_contain_other_keys(self):
        data = json.loads(tool_error("oops"))
        self.assertEqual(list(data.keys()), ["error"])


class RegistryRegisterTests(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()

    def test_register_and_dispatch(self):
        def handler(args, **kwargs):
            return tool_result(value=args["x"] * 2)

        schema = {
            "name": "double",
            "description": "double a number",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}},
        }
        self.reg.register(name="double", schema=schema, handler=handler, check_fn=lambda: True)
        out = self.reg.dispatch("double", {"x": 5})
        self.assertEqual(json.loads(out), {"value": 10})

    def test_dispatch_unknown_tool_returns_error(self):
        out = self.reg.dispatch("nope", {})
        self.assertEqual(json.loads(out), {"error": "unknown tool: nope"})

    def test_register_duplicate_name_overwrites(self):
        """Re-registering the same name is allowed (overwrites) — required for
        Streamlit auto-reload, where the module is re-imported and the
        registration side-effect re-runs."""
        self.reg.register(name="x", schema={"name": "x"},
                          handler=lambda a, **k: tool_result(value="first"),
                          check_fn=lambda: True)
        # Second registration with the same name should NOT raise
        self.reg.register(name="x", schema={"name": "x"},
                          handler=lambda a, **k: tool_result(value="second"),
                          check_fn=lambda: True)
        # And the second handler should now be active
        out = self.reg.dispatch("x", {})
        self.assertEqual(json.loads(out), {"value": "second"})

    def test_check_fn_false_blocks_dispatch(self):
        def handler(args, **kwargs):
            return tool_result(value="ran")

        self.reg.register(
            name="gated",
            schema={"name": "gated"},
            handler=handler,
            check_fn=lambda: False,
        )
        out = self.reg.dispatch("gated", {})
        data = json.loads(out)
        self.assertIn("not available", data["error"])

    def test_handler_exception_caught_and_returned_as_error(self):
        def handler(args, **kwargs):
            raise RuntimeError("boom")

        self.reg.register(name="bad", schema={"name": "bad"}, handler=handler, check_fn=lambda: True)
        out = self.reg.dispatch("bad", {})
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertIn("boom", data["error"])

    def test_dispatch_passes_kwargs_to_handler(self):
        seen = {}

        def handler(args, **kwargs):
            seen.update(kwargs)
            return tool_result(ok=True)

        self.reg.register(name="k", schema={"name": "k"}, handler=handler, check_fn=lambda: True)
        self.reg.dispatch("k", {}, session_id="abc", task_id="t1")
        self.assertEqual(seen, {"session_id": "abc", "task_id": "t1"})


class RegistryListSchemasTests(unittest.TestCase):
    def test_list_schemas_returns_empty_when_nothing_registered(self):
        self.assertEqual(ToolRegistry().list_schemas(), [])

    def test_list_schemas_preserves_order_of_registration(self):
        reg = ToolRegistry()
        for i in range(3):
            reg.register(
                name=f"t{i}",
                schema={"name": f"t{i}", "description": f"d{i}", "parameters": {}},
                handler=lambda a, **k: "ok",
                check_fn=lambda: True,
            )
        names = [s["name"] for s in reg.list_schemas()]
        self.assertEqual(names, ["t0", "t1", "t2"])


class RegistryMetaTests(unittest.TestCase):
    """P0 能力治理: 每个工具带 read_only / risk 元数据, 供管理页分级展示."""

    def _reg(self):
        return ToolRegistry()

    def test_register_defaults_to_read_only_low_risk(self):
        reg = self._reg()
        reg.register(name="t", schema={"name": "t"}, handler=lambda a, **k: "ok",
                     check_fn=lambda: True)
        meta = reg.list_meta()[0]
        self.assertTrue(meta["read_only"])
        self.assertEqual(meta["risk"], "low")

    def test_register_explicit_write_high_risk_stored(self):
        reg = self._reg()
        reg.register(name="t", schema={"name": "t"}, handler=lambda a, **k: "ok",
                     check_fn=lambda: True, read_only=False, risk="high")
        meta = reg.list_meta()[0]
        self.assertFalse(meta["read_only"])
        self.assertEqual(meta["risk"], "high")

    def test_list_meta_returns_governance_fields(self):
        reg = self._reg()
        reg.register(name="t", schema={"name": "t"}, handler=lambda a, **k: "ok",
                     check_fn=lambda: True, toolset="ops", emoji="🔧")
        meta = reg.list_meta()[0]
        self.assertEqual(meta["name"], "t")
        self.assertEqual(meta["toolset"], "ops")
        self.assertEqual(meta["emoji"], "🔧")
        self.assertEqual(meta["schema"]["name"], "t")

    def test_run_selfheal_marked_write_high_risk(self):
        """唯一受控写入口必须显式标注 write/high, 管理页与对话过滤依赖此标记."""
        from hermes.tools.selfheal import tools  # noqa: F401  (注册副作用)
        meta = {m["name"]: m for m in registry.list_meta()}
        self.assertIn("run_selfheal", meta)
        self.assertFalse(meta["run_selfheal"]["read_only"])
        self.assertEqual(meta["run_selfheal"]["risk"], "high")


class RegistryChatToolsTests(unittest.TestCase):
    """P3 对话「读全开」: 白名单(凭据边界)内只读工具自动全开 + 唯一写入口 selfheal."""

    def test_chat_tools_include_readonly_whitelist_and_selfheal(self):
        from hermes.tools.selfheal import tools  # noqa: F401  (注册副作用)
        names = {t["name"] for t in registry.list_chat_tools()}
        self.assertIn("run_selfheal", names)       # 唯一受控写入口
        self.assertIn("check_k8s_pods", names)     # 白名单只读观测
        self.assertIn("scan_log_cleanup", names)
        self.assertIn("list_servers", names)
        # bare SSH(任意 host/password) 不在白名单 → 不得进入对话集
        self.assertNotIn("check_resources", names)
        self.assertNotIn("list_services", names)

    def test_write_tool_never_exposed_even_if_whitelisted(self):
        """写工具即使误入白名单(read_only=False)也不得按只读暴露."""
        from unittest.mock import patch

        reg = ToolRegistry()
        reg.register(name="write_tool", schema={"name": "write_tool"},
                     handler=lambda a, **k: "ok", check_fn=lambda: True,
                     read_only=False, risk="high")
        with patch("hermes.tools.registry.is_chat_visible", return_value=True):
            names = [t["name"] for t in reg.list_chat_tools()]
        self.assertNotIn("write_tool", names)


if __name__ == "__main__":
    unittest.main()
