"""Tests for hermes.feishu.bot — queue + dispatch (no real SDK)."""
import unittest
from unittest.mock import MagicMock

from hermes.feishu.bot import EMOJI_PROCESSING, FeishuBot


class FeishuBotTests(unittest.TestCase):
    def setUp(self):
        self.send_fn = MagicMock()  # (open_id, text) -> None
        self.reply_fn = MagicMock()  # (message_id, text) -> None
        self.add_reaction_fn = MagicMock(return_value="rxn_1")  # (message_id, emoji) -> reaction_id
        self.bot = FeishuBot(
            whitelist=["ou_abc"],
            send_message=self.send_fn,
            reply_message=self.reply_fn,
            add_reaction=self.add_reaction_fn,
            remove_reaction=MagicMock(),
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
        # 不再回复「处理中」文本
        self.send_fn.assert_not_called()

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
        # 不再回复「处理中」文本(群聊 reply 也不应被调用)
        self.reply_fn.assert_not_called()

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

    def test_immediate_p2p_reply_failure_still_enqueues(self):
        # 「处理中」文本回复已移除,这里验证:即使没有即时文本回复,任务仍正常入队
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "p2p", "message_type": "text",
                            "content": '{"text":"check disk"}'},
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 1)

    def test_immediate_group_reply_failure_still_enqueues(self):
        # 群聊同样:无即时文本回复,任务仍正常入队
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "group", "message_type": "text",
                            "content": '{"text":"check disk"}',
                            "mentions": [{"key": "k"}],
                            "message_id": "om_123"},
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 1)

    # ---- 表情(处理中 ⏳)相关 ----

    def test_adds_processing_reaction_on_group_message(self):
        # 收到群聊消息后,应在用户消息上添加「处理中」表情,并记录 reaction_id 到任务
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "group", "message_type": "text",
                            "content": '{"text":"check disk"}',
                            "mentions": [{"key": "k"}],
                            "message_id": "om_123"},
            }
        )
        self.add_reaction_fn.assert_called_once_with("om_123", EMOJI_PROCESSING)
        task = self.bot.queue.get()
        self.assertEqual(task["reaction_id"], "rxn_1")

    def test_reaction_failure_does_not_block_enqueue(self):
        # 添加表情失败只记日志,不影响入队
        self.add_reaction_fn.side_effect = RuntimeError("api down")
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "p2p", "message_type": "text",
                            "content": '{"text":"hi"}',
                            "message_id": "om_123"},
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 1)
        task = self.bot.queue.get()
        self.assertIsNone(task["reaction_id"])

    def test_p2p_without_message_id_skips_reaction(self):
        # 无 message_id 的消息无法加 reaction,跳过且不影响入队
        self.bot.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "p2p", "message_type": "text",
                            "content": '{"text":"hi"}'},
            }
        )
        self.assertEqual(self.bot.queue.qsize(), 1)
        self.add_reaction_fn.assert_not_called()
        task = self.bot.queue.get()
        self.assertIsNone(task["reaction_id"])

    def test_without_add_reaction_injected_skips_gracefully(self):
        # 未注入 add_reaction(旧配置)时,不应报错,任务照常入队
        bot_no_reaction = FeishuBot(
            whitelist=["ou_abc"],
            send_message=self.send_fn,
            reply_message=self.reply_fn,
        )
        bot_no_reaction.handle_event(
            {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"chat_type": "p2p", "message_type": "text",
                            "content": '{"text":"hi"}',
                            "message_id": "om_123"},
            }
        )
        self.assertEqual(bot_no_reaction.queue.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
