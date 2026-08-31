"""Tests for hermes.llm_agent."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Force-set a dummy LLM key so llm_agent can construct
os.environ["TOKENHUB_API_KEY"] = "sk-test-dummy"

# audit removed - was hermes.opslib.audit (legacy)
from hermes.data import db, models  # noqa: E402
from hermes.agents import chat as llm_agent  # noqa: E402
from hermes.data.models import Conversation, RunRecord, Server, SSHCredential  # noqa: E402
from hermes.tools.registry import registry  # noqa: E402


def _wipe():
    db.init_db()
    with db.session_scope() as s:
        s.query(RunRecord).delete()
        s.query(Conversation).delete()
        s.query(Server).delete()
        s.query(SSHCredential).delete()


def _add_server(name="web-01"):
    with db.session_scope() as s:
        cred = models.SSHCredential(
            name=f"{name}-cred", username="root", password="x", is_default=True,
        )
        s.add(cred)
        s.flush()
        s.add(models.Server(name=name, host="10.0.0.1", ssh_credential_id=cred.id))
    with db.session_scope() as s:
        return s.query(Server).filter_by(name=name).one()


def _make_text_response(content: str) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": content,
                "tool_calls": None,
            }
        }]
    }


def _make_tool_response(name: str, args: dict, call_id: str) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                }],
            }
        }]
    }


def _fake_client(responses: list[dict]) -> MagicMock:
    """A TokenHubClient stub that returns `responses` in order."""
    c = MagicMock()
    c.chat = MagicMock(side_effect=responses)
    return c


class ChatNewConversationTests(unittest.TestCase):
    def setUp(self):
        _wipe()

    def test_creates_new_conversation_when_id_is_none(self):
        client = _fake_client([_make_text_response("hello there")])
        result = llm_agent.chat("hi", client=client)
        self.assertIsNotNone(result["conversation_id"])
        self.assertEqual(result["reply"], "hello there")
        self.assertEqual(result["rounds"], 1)

    def test_saves_messages_to_db(self):
        client = _fake_client([_make_text_response("hi back")])
        result = llm_agent.chat("hi", client=client)
        with db.session_scope() as s:
            c = s.get(Conversation, result["conversation_id"])
            self.assertIsNotNone(c)
            messages = json.loads(c.messages_json)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[0]["content"], "hi")
            self.assertEqual(messages[1]["role"], "assistant")
            self.assertEqual(messages[1]["content"], "hi back")

    def test_auto_titles_conversation(self):
        client = _fake_client([_make_text_response("ok")])
        result = llm_agent.chat("check disk on web-01 please", client=client)
        with db.session_scope() as s:
            c = s.get(Conversation, result["conversation_id"])
            self.assertEqual(c.title, "check disk on web-01 please")


class ChatExistingConversationTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        with db.session_scope() as s:
            s.add(Conversation(id=42, title="pre-existing", messages_json="[]", total_runs=0))

    def test_continues_existing_conversation(self):
        client = _fake_client([_make_text_response("follow-up answer")])
        result = llm_agent.chat("what about db-01?", conversation_id=42, client=client)
        self.assertEqual(result["conversation_id"], 42)
        with db.session_scope() as s:
            c = s.get(Conversation, 42)
            messages = json.loads(c.messages_json)
            self.assertEqual(messages[0]["content"], "what about db-01?")
            self.assertEqual(messages[1]["content"], "follow-up answer")

    def test_raises_for_missing_conversation(self):
        client = _fake_client([_make_text_response("x")])
        with self.assertRaises(ValueError):
            llm_agent.chat("hi", conversation_id=99999, client=client)


class ChatToolCallTests(unittest.TestCase):
    def setUp(self):
        _wipe()
        self.s1 = _add_server("web-01")

    def test_calls_tool_and_persists_run(self):
        # Round 1: LLM calls list_servers
        # Round 2: LLM calls check_disk_usage
        # Round 3: LLM gives final answer
        r1 = _make_tool_response("list_servers", {}, "call_1")
        r2 = _make_tool_response("check_disk_usage", {"server_id": self.s1.id}, "call_2")
        r3 = _make_text_response("Web-01 disk looks fine.")
        client = _fake_client([r1, r2, r3])

        fake_result = json.dumps({
            "mounts": [{"mount": "/", "use_pct": 50}],
            "summary": {"total_mounts": 1, "warning_count": 0, "critical_count": 0},
        })
        # chat.py dispatches through the shared registry singleton; patch its
        # dispatch so both list_servers and check_disk_usage return fake data.
        with patch.object(registry, "dispatch", return_value=fake_result) as mock_dispatch:
            result = llm_agent.chat("check web-01", client=client)

        self.assertEqual(result["reply"], "Web-01 disk looks fine.")
        self.assertEqual(result["rounds"], 3)
        self.assertEqual(len(result["tool_calls"]), 2)
        self.assertEqual(result["tool_calls"][0]["name"], "list_servers")
        self.assertEqual(result["tool_calls"][1]["name"], "check_disk_usage")
        # both tool calls went through registry.dispatch
        self.assertEqual(mock_dispatch.call_count, 2)
        # total_runs was incremented
        with db.session_scope() as s:
            c = s.get(Conversation, result["conversation_id"])
            self.assertEqual(c.total_runs, 1)

    def test_max_rounds_returns_fallback(self):
        # 5 tool-call rounds, no final text
        responses = [_make_tool_response("list_servers", {}, f"c{i}") for i in range(5)]
        client = _fake_client(responses)
        result = llm_agent.chat("loop forever", client=client, max_rounds=5)
        self.assertIn("max tool rounds", result["reply"])


class MissingApiKeyTests(unittest.TestCase):
    def test_raises_if_key_missing(self):
        with patch.dict(os.environ, {"TOKENHUB_API_KEY": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                llm_agent.chat("hi")


if __name__ == "__main__":
    unittest.main()
