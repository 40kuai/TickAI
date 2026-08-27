"""Tests for hermes.feishu.tasks — worker loop (no real SDK/LLM)."""
import unittest
from unittest.mock import MagicMock, patch

from hermes.feishu.tasks import FeishuWorker


class FeishuWorkerTests(unittest.TestCase):
    def setUp(self):
        self.send_fn = MagicMock()
        self.reply_fn = MagicMock()
        self.chat_fn = MagicMock(return_value={
            "conversation_id": 42,
            "reply": "ok, checked",
            "messages": [],
            "tool_calls": [],
            "rounds": 1,
        })
        self.worker = FeishuWorker(
            send_message=self.send_fn,
            reply_message=self.reply_fn,
            chat_fn=self.chat_fn,
            set_conversation=MagicMock(),
        )

    def test_process_p2p_task_calls_chat_and_sends(self):
        task = {
            "open_id": "ou_abc",
            "text": "check disk",
            "chat_type": "p2p",
            "message_id": None,
            "conversation_id": None,
        }
        self.worker.process_task(task)
        self.chat_fn.assert_called_once()
        self.assertEqual(self.chat_fn.call_args[1]["conversation_id"], None)
        self.send_fn.assert_called_once_with("ou_abc", "ok, checked")
        self.reply_fn.assert_not_called()

    def test_process_group_task_replies_to_message(self):
        task = {
            "open_id": "ou_abc",
            "text": "check disk",
            "chat_type": "group",
            "message_id": "om_123",
            "conversation_id": None,
        }
        self.worker.process_task(task)
        self.reply_fn.assert_called_once_with("om_123", "ok, checked")
        self.send_fn.assert_not_called()

    def test_chat_error_returns_error_message(self):
        self.chat_fn.side_effect = RuntimeError("LLM not configured")
        task = {
            "open_id": "ou_abc",
            "text": "hi",
            "chat_type": "p2p",
            "message_id": None,
            "conversation_id": None,
        }
        self.worker.process_task(task)
        self.send_fn.assert_called_once()
        self.assertIn("错误", self.send_fn.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
