"""Tests for tools.registry — tool_result / tool_error / ToolRegistry."""
import json
import unittest

from hermes.tools.registry import ToolRegistry, tool_error, tool_result


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


if __name__ == "__main__":
    unittest.main()
