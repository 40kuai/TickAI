"""Tests for caller.py helpers — env loading, tools payload, message flow."""
import contextlib
import io
import json
import unittest

import httpx

import hermes.core.llm
from hermes.core.llm import (
    TokenHubClient,
    _log_llm_error,
    _log_llm_request,
    _log_llm_response,
    build_tools_payload,
    parse_env_file,
    run_conversation,
)


class ParseEnvFileTests(unittest.TestCase):
    def test_parses_simple_key_value(self):
        text = "TOKENHUB_API_KEY=sk-abc\nTOKENHUB_MODEL=auto\n"
        env = parse_env_file(text)
        self.assertEqual(env["TOKENHUB_API_KEY"], "sk-abc")
        self.assertEqual(env["TOKENHUB_MODEL"], "auto")

    def test_skips_blank_lines_and_comments(self):
        text = "# this is a comment\n\nKEY=value\n"
        env = parse_env_file(text)
        self.assertEqual(env, {"KEY": "value"})

    def test_strips_quotes_around_value(self):
        text = 'K1="quoted value"\nK2=\'single\'\n'
        env = parse_env_file(text)
        self.assertEqual(env["K1"], "quoted value")
        self.assertEqual(env["K2"], "single")

    def test_handles_inline_comment(self):
        text = "KEY=value # trailing\n"
        env = parse_env_file(text)
        self.assertEqual(env["KEY"], "value")

    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(parse_env_file(""), {})


class BuildToolsPayloadTests(unittest.TestCase):
    def test_wraps_schema_in_function_envelope(self):
        schemas = [
            {
                "name": "roll_dice",
                "description": "roll a dice",
                "parameters": {"type": "object", "properties": {"sides": {"type": "integer"}}},
            }
        ]
        payload = build_tools_payload(schemas)
        self.assertEqual(len(payload), 1)
        tool = payload[0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["function"]["name"], "roll_dice")
        self.assertEqual(tool["function"]["description"], "roll a dice")
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


class _MockTransport(httpx.BaseTransport):
    """Pluggable httpx transport. `responder(req)` returns an httpx.Response."""

    def __init__(self, responder):
        self._responder = responder
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)


class ToolCallLoopIntegrationTests(unittest.TestCase):
    """End-to-end test of run_conversation with a mocked TokenHub."""

    def _make_client(self, transport) -> TokenHubClient:
        # Bypass TokenHubClient.__init__ requirements by injecting a custom client
        client = TokenHubClient.__new__(TokenHubClient)
        client.api_key = "sk-test"
        client.base_url = "https://mock.local/plan/v3"
        client.model = "deepseek-v4-flash"
        client.timeout = 5.0
        client.verbose = False  # silence LLM logging in these tests
        client._transport = transport
        return client

    def test_first_response_is_tool_call_second_is_text(self):
        # Round 1: LLM asks to roll dice
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
        # Round 2: LLM returns final text
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
        client = self._make_client(transport)

        # Patch httpx.Client to use our transport
        original_client = httpx.Client
        httpx.Client = lambda *a, **kw: original_client(transport=transport)
        try:
            result = run_conversation(client, "Roll a d20 for me")
        finally:
            httpx.Client = original_client

        self.assertEqual(result, "You rolled a 14 on a d20.")
        self.assertEqual(len(transport.requests), 2)

        # Verify the second request includes the tool result
        second_body = json.loads(transport.requests[1].content)
        msgs = second_body["messages"]
        # Last message should be role=tool with the dice result
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
        client = self._make_client(transport)
        original_client = httpx.Client
        httpx.Client = lambda *a, **kw: original_client(transport=transport)
        try:
            result = run_conversation(client, "hello")
        finally:
            httpx.Client = original_client
        self.assertEqual(result, "Hi there.")
        self.assertEqual(len(transport.requests), 1)

    def test_sends_tools_payload_when_tools_registered(self):
        # caller.py imports tools.dice and tools.disk at module load, so both
        # should be in the registry. Verify the wire payload wraps each schema
        # in the OpenAI function envelope.
        schemas = caller.registry.list_schemas()
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
        # LLM asks for an invalid dice roll (sides out of range)
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
        # LLM acknowledges the error and returns final text
        round2 = {
            "choices": [{
                "message": {"role": "assistant", "content": "I can't roll a 1-sided die."}
            }]
        }
        responses = [round1, round2]
        transport = _MockTransport(
            lambda req: httpx.Response(200, json=responses.pop(0))
        )
        client = self._make_client(transport)
        original_client = httpx.Client
        httpx.Client = lambda *a, **kw: original_client(transport=transport)
        try:
            result = run_conversation(client, "roll a 1-sided die")
        finally:
            httpx.Client = original_client
        self.assertEqual(result, "I can't roll a 1-sided die.")
        # Second request's tool message should contain the error envelope
        second_body = json.loads(transport.requests[1].content)
        tool_msg = [m for m in second_body["messages"] if m.get("role") == "tool"][0]
        payload = json.loads(tool_msg["content"])
        self.assertIn("error", payload)
        self.assertIn("sides", payload["error"])


class LlmLoggingTests(unittest.TestCase):
    """Verify the per-call LLM logging is informative and goes to stderr."""

    def _capture(self, func, *args, **kwargs) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            func(*args, **kwargs)
        return buf.getvalue()

    def test_log_request_includes_endpoint_model_and_payload(self):
        out = self._capture(
            _log_llm_request,
            payload={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
            endpoint="https://tokenhub.tencentmaas.com/plan/v3/chat/completions",
        )
        self.assertIn("[LLM CALL]", out)
        self.assertIn("https://tokenhub.tencentmaas.com/plan/v3/chat/completions", out)
        self.assertIn("deepseek-v4-flash", out)
        self.assertIn("messages: 1", out)
        # payload should be a valid JSON dump
        self.assertIn("\"role\": \"user\"", out)
        self.assertIn("\"content\": \"hi\"", out)

    def test_log_request_counts_tools(self):
        out = self._capture(
            _log_llm_request,
            payload={"model": "m", "messages": [], "tools": [{"type": "function"}, {"type": "function"}]},
            endpoint="http://x",
        )
        self.assertIn("tools: 2", out)

    def test_log_request_omits_tools_line_when_no_tools(self):
        out = self._capture(
            _log_llm_request,
            payload={"model": "m", "messages": []},
            endpoint="http://x",
        )
        self.assertNotIn("tools:", out)

    def test_log_response_includes_latency_tokens_and_finish_reason(self):
        resp = {
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }
        out = self._capture(_log_llm_response, resp=resp, elapsed_ms=1234.5, status_code=200)
        self.assertIn("[LLM RESP]", out)
        self.assertIn("1234ms", out)
        self.assertIn("200", out)
        self.assertIn("finish_reason: stop", out)
        self.assertIn("prompt=100", out)
        self.assertIn("completion=20", out)
        self.assertIn("total=120", out)
        self.assertIn("content: hello", out)

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
        out = self._capture(_log_llm_response, resp=resp, elapsed_ms=500.0, status_code=200)
        self.assertIn("finish_reason: tool_calls", out)
        self.assertIn("tool_calls: 1", out)
        self.assertIn("call_abc", out)
        self.assertIn("roll_dice", out)
        self.assertIn('"sides": 20', out)

    def test_log_response_handles_missing_usage(self):
        resp = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        out = self._capture(_log_llm_response, resp=resp, elapsed_ms=100.0, status_code=200)
        self.assertIn("[LLM RESP]", out)
        # Should not crash; just skip the tokens line
        self.assertIn("finish_reason: stop", out)

    def test_log_response_truncates_very_long_content(self):
        long_content = "x" * 1000
        resp = {"choices": [{"finish_reason": "stop", "message": {"content": long_content}}]}
        out = self._capture(_log_llm_response, resp=resp, elapsed_ms=100.0, status_code=200)
        # Full content should NOT be printed verbatim — should be truncated
        self.assertNotIn("x" * 1000, out)
        self.assertIn("xxx", out)  # at least some of it
        self.assertIn("truncated", out.lower())

    def test_log_error_includes_exception_type_and_message(self):
        out = self._capture(
            _log_llm_error,
            exc=ConnectionError("server unreachable"),
            elapsed_ms=234.5,
        )
        self.assertIn("[LLM ERROR]", out)
        self.assertIn("234ms", out)
        self.assertIn("ConnectionError", out)
        self.assertIn("server unreachable", out)

    def test_log_error_handles_http_4xx_with_body(self):
        class FakeError(RuntimeError):
            pass
        out = self._capture(
            _log_llm_error,
            exc=FakeError("HTTP 401: invalid api key"),
            elapsed_ms=100.0,
        )
        self.assertIn("[LLM ERROR]", out)
        self.assertIn("HTTP 401", out)
        self.assertIn("invalid api key", out)

    def test_verbose_false_silences_request_log(self):
        from hermes.core.llm import TokenHubClient as THC
        real = THC.__new__(THC)
        real.api_key = "sk-test"
        real.base_url = "https://mock/plan/v3"
        real.model = "m"
        real.timeout = 5.0
        real.verbose = False  # silenced

        resp_json = {"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}
        transport = _MockTransport(lambda req: httpx.Response(200, json=resp_json))

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            real.chat([{"role": "user", "content": "ping"}], client_factory=lambda **kw: httpx.Client(transport=transport))
        self.assertEqual(buf.getvalue(), "")

    def test_chat_invokes_log_request_and_response_on_success(self):
        from hermes.core.llm import TokenHubClient as THC
        real = THC.__new__(THC)
        real.api_key = "sk-test"
        real.base_url = "https://mock/plan/v3"
        real.model = "m"
        real.timeout = 5.0
        real.verbose = True

        resp_json = {"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        transport = _MockTransport(lambda req: httpx.Response(200, json=resp_json))

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            real.chat(
                [{"role": "user", "content": "ping"}],
                client_factory=lambda **kw: httpx.Client(transport=transport),
            )
        out = buf.getvalue()
        self.assertIn("[LLM CALL]", out)
        self.assertIn("https://mock/plan/v3/chat/completions", out)
        self.assertIn("[LLM RESP]", out)
        self.assertIn("finish_reason: stop", out)
        self.assertIn("content: hi", out)

    def test_chat_invokes_log_error_on_http_4xx(self):
        from hermes.core.llm import TokenHubClient as THC
        real = THC.__new__(THC)
        real.api_key = "sk-test"
        real.base_url = "https://mock/plan/v3"
        real.model = "m"
        real.timeout = 5.0
        real.verbose = True

        transport = _MockTransport(lambda req: httpx.Response(401, text="invalid api key"))

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with self.assertRaises(RuntimeError):
                real.chat(
                    [{"role": "user", "content": "x"}],
                    client_factory=lambda **kw: httpx.Client(transport=transport),
                )
        out = buf.getvalue()
        self.assertIn("[LLM CALL]", out)
        self.assertIn("[LLM ERROR]", out)
        self.assertIn("401", out)
        self.assertIn("invalid api key", out)


if __name__ == "__main__":
    unittest.main()
