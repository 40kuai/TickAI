"""Tests for hermes.feishu.bot — queue + dispatch (no real SDK)."""
import unittest
from unittest.mock import MagicMock

from hermes.feishu.bot import FeishuBot


class FeishuBotTests(unittest.TestCase):
    def setUp(self):
        self.send_fn = MagicMock()  # (open_id, text) -> None
        self.reply_fn = MagicMock()  # (message_id, text) -> None
        self.bot = FeishuBot(
            whitelist=["ou_abc"],
            send_message=self.send_fn,
            reply_message=self.reply_fn,
        )

    def test_whitelisted_p2p_enqueued(self):
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": '{"text":"check disk"}',
                },
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 1)
        # 立即回「处理中」
        self.send_fn.assert_called_once()
        self.assertEqual(self.send_fn.call_args[0][1], "正在处理,请稍候…")

    def test_non_whitelisted_not_enqueued(self):
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_hacker"}},
                "message": {"chat_type": "p2p", "message_type": "text",
                            "content": '{"text":"x"}'},
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 0)
        self.send_fn.assert_not_called()

    def test_group_with_mention_enqueued_and_replies(self):
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {
                    "chat_type": "group",
                    "message_type": "text",
                    "content": '{"text":"check disk"}',
                    "mentions": [{"key": "k"}],
                    "message_id": "om_123",
                },
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 1)
        self.reply_fn.assert_called_once()
        self.assertEqual(self.reply_fn.call_args[0][0], "om_123")

    def test_conversation_mapping_preserved(self):
        # 同一 open_id 两次消息,conversation_id 复用
        self.bot.set_conversation("ou_abc", 42)
        item = self.bot._build_task({"sender": {"sender_id": {"open_id": "ou_abc"}}}, "hi")
        self.assertEqual(item["conversation_id"], 42)

    def test_empty_text_ignored(self):
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "p2p", "message_type": "text",
                            "content": '{"text":""}'},
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
