"""Tests for hermes.core.llm — TokenHubClient, tool-call loop, logging."""
import contextlib
import io
import json
import unittest

import httpx

from hermes.core.llm import (
    TokenHubClient,
    build_tools_payload,
    run_conversation,
)
from hermes.core.llm_logging import (
    log_error,
    log_request,
    log_response,
)
from hermes.tools.registry import registry


# ============================================================
# build_tools_payload
# ============================================================

class BuildToolsPayloadTests(unittest.TestCase):
    def test_wraps_schema_in_function_envelope(self):
        schemas = [{
            "name": "foo",
            "description": "a foo",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}},
        }]
        payload = build_tools_payload(schemas)
        self.assertEqual(len(payload), 1)
        tool = payload[0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "foo")
        self.assertEqual(tool["function"]["description"], "a foo")
        self.assertIn("properties", tool["function"]["parameters"])

    def test_empty_schemas_returns_empty_list(self):
        self.assertEqual(build_tools_payload([]), [])

    def test_preserves_schema_field_order_independently(self):
        schemas = [
            {"name": "a", "description": "A", "parameters": {}},
            {"name": "b", "description": "B", "parameters": {}},
        ]
        payload = build_tools_payload(schemas)
        self.assertEqual([t["function"]["name"] for t in payload], ["a", "b"])


# ============================================================
# run_conversation tool-call loop (mocked HTTP transport)
# ============================================================

class _MockTransport(httpx.BaseTransport):
    """Pluggable httpx transport. `responder(req)` returns an httpx.Response."""

    def __init__(self, responder):
        self._responder = responder
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


def _make_client(transport, verbose: bool = False) -> TokenHubClient:
    """Build a real TokenHubClient whose underlying httpx client uses `transport`."""
    client = TokenHubClient.__new__(TokenHubClient)
    client.api_key = "sk-test"
    client.base_url = "https://mock.local/plan/v3"
    client.model = "deepseek-v4-flash"
    client.timeout = 5.0
    client.verbose = verbose
    client._client = httpx.Client(transport=transport)
    return client


def _dice_handler(args: dict, **kwargs) -> str:
    """Fake registered tool used by the loop tests."""
    sides = args.get("sides", 20)
    if sides < 2:
        return json.dumps({"error": "sides must be >= 2"})
    return json.dumps({"sides": sides, "rolls": [7]})


class ToolCallLoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Register a controllable fake tool for the loop tests.
        registry.register(
            name="roll_dice",
            schema={
                "name": "roll_dice",
                "description": "roll a dice",
                "parameters": {"type": "object", "properties": {"sides": {"type": "integer"}}},
            },
            handler=_dice_handler,
            check_fn=lambda: True,
        )

    def tearDown(self):
        registry._tools.pop("roll_dice", None)

    def test_first_response_is_tool_call_second_is_text(self):
        round1 = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "roll_dice",
                            "arguments": json.dumps({"sides": 20}),
                        },
                    }],
                }
            }]
        }
        round2 = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "You rolled a 14 on a d20.",
                }
            }]
        }
        responses = [round1, round2]
        transport = _MockTransport(
            lambda req: httpx.Response(200, json=responses.pop(0))
        )
        client = _make_client(transport)

        result = run_conversation(client, "Roll a d20 for me")

        self.assertEqual(result, "You rolled a 14 on a d20.")
        self.assertEqual(len(transport.requests), 2)

        # The second request includes the tool result message
        second_body = json.loads(transport.requests[1].content)
        msgs = second_body["messages"]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_1")
        result_payload = json.loads(tool_msgs[0]["content"])
        self.assertEqual(result_payload["sides"], 20)
        self.assertEqual(len(result_payload["rolls"]), 1)
        self.assertTrue(1 <= result_payload["rolls"][0] <= 20)

    def test_no_tool_call_returns_content_immediately(self):
        transport = _MockTransport(
            lambda req: httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "Hi there."}}]
            })
        )
        client = _make_client(transport)
        result = run_conversation(client, "hello")
        self.assertEqual(result, "Hi there.")
        self.assertEqual(len(transport.requests), 1)

    def test_sends_tools_payload_when_tools_registered(self):
        schemas = registry.list_schemas()
        names = [s["name"] for s in schemas]
        self.assertIn("roll_dice", names)
        self.assertIn("check_disk_usage", names)
        payload = build_tools_payload(schemas)
        self.assertTrue(len(payload) >= 2)
        for tool in payload:
            self.assertEqual(tool["type"], "function")
            self.assertIn("function", tool)
            self.assertIn("name", tool["function"])

    def test_handler_error_becomes_tool_message_not_exception(self):
        round1 = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_bad",
                        "type": "function",
                        "function": {
                            "name": "roll_dice",
                            "arguments": json.dumps({"sides": 1}),  # below min
                        },
                    }],
                }
            }]
        }
        round2 = {
            "choices": [{
                "message": {"role": "assistant", "content": "I can't roll a 1-sided die."}
            }]
        }
        responses = [round1, round2]
        transport = _MockTransport(
            lambda req: httpx.Response(200, json=responses.pop(0))
        )
        client = _make_client(transport)
        result = run_conversation(client, "roll a 1-sided die")
        self.assertEqual(result, "I can't roll a 1-sided die.")
        second_body = json.loads(transport.requests[1].content)
        tool_msg = [m for m in second_body["messages"] if m.get("role") == "tool"][0]
        payload = json.loads(tool_msg["content"])
        self.assertIn("error", payload)
        self.assertIn("sides", payload["error"])


# ============================================================
# LLM logging (log_request / log_response / log_error)
# ============================================================

class LlmLoggingTests(unittest.TestCase):
    def _capture(self, func, *args, **kwargs) -> str:
        """Capture stdout — the logging helpers print to stdout."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            func(*args, **kwargs)
        return buf.getvalue()

    def test_log_request_includes_endpoint_model_and_message_count(self):
        out = self._capture(
            log_request,
            payload={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
            endpoint="https://tokenhub.tencentmaas.com/plan/v3/chat/completions",
            model="deepseek-v4-flash",
        )
        self.assertIn("[LLM CALL]", out)
        self.assertIn("https://tokenhub.tencentmaas.com/plan/v3/chat/completions", out)
        self.assertIn("deepseek-v4-flash", out)
        self.assertIn("messages: 1", out)

    def test_log_request_counts_tools(self):
        out = self._capture(
            log_request,
            payload={"model": "m", "messages": [], "tools": [{"type": "function"}, {"type": "function"}]},
            endpoint="http://x",
            model="m",
        )
        self.assertIn("tools: 2", out)

    def test_log_request_omits_tools_line_when_no_tools(self):
        out = self._capture(
            log_request,
            payload={"model": "m", "messages": []},
            endpoint="http://x",
            model="m",
        )
        self.assertNotIn("tools:", out)

    def test_log_response_includes_latency_tokens_and_finish_reason(self):
        resp = {
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }
        out = self._capture(log_response, resp=resp, elapsed_ms=1234, status_code=200)
        self.assertIn("[LLM RESP]", out)
        self.assertIn("1234ms", out)
        self.assertIn("200", out)
        self.assertIn("finish_reason: stop", out)
        self.assertIn("prompt=100", out)
        self.assertIn("completion=20", out)
        self.assertIn("total=120", out)

    def test_log_response_handles_tool_calls(self):
        resp = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "function": {"name": "roll_dice", "arguments": "{\"sides\": 20}"},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65},
        }
        out = self._capture(log_response, resp=resp, elapsed_ms=500, status_code=200)
        self.assertIn("finish_reason: tool_calls", out)
        self.assertIn("tool_calls: 1", out)
        self.assertIn("call_abc", out)
        self.assertIn("roll_dice", out)
        self.assertIn('"sides": 20', out)

    def test_log_response_handles_missing_usage(self):
        resp = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        out = self._capture(log_response, resp=resp, elapsed_ms=100, status_code=200)
        self.assertIn("[LLM RESP]", out)
        self.assertIn("finish_reason: stop", out)

    def test_log_error_includes_exception_type_and_message(self):
        out = self._capture(log_error, exc=ConnectionError("server unreachable"), elapsed_ms=234)
        self.assertIn("[LLM ERROR]", out)
        self.assertIn("234ms", out)
        self.assertIn("ConnectionError", out)
        self.assertIn("server unreachable", out)


# ============================================================
# TokenHubClient.chat behavior
# ============================================================

class TokenHubClientChatTests(unittest.TestCase):
    def test_chat_success_returns_json_no_output(self):
        resp_json = {"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}
        transport = _MockTransport(lambda req: httpx.Response(200, json=resp_json))
        client = _make_client(transport, verbose=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = client.chat([{"role": "user", "content": "ping"}])
        self.assertEqual(out["choices"][0]["message"]["content"], "hi")
        self.assertEqual(buf.getvalue(), "")

    def test_chat_logs_error_on_http_4xx(self):
        transport = _MockTransport(lambda req: httpx.Response(401, text="invalid api key"))
        client = _make_client(transport, verbose=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(RuntimeError):
                client.chat([{"role": "user", "content": "x"}])
        out = buf.getvalue()
        self.assertIn("[LLM ERROR]", out)
        self.assertIn("401", out)
        self.assertIn("invalid api key", out)

    def test_verbose_false_silences_error_log(self):
        transport = _MockTransport(lambda req: httpx.Response(401, text="invalid api key"))
        client = _make_client(transport, verbose=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(RuntimeError):
                client.chat([{"role": "user", "content": "x"}])
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
