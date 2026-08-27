"""Tests for hermes.feishu.tasks — worker loop (no real SDK/LLM)."""
import unittest
from unittest.mock import MagicMock, patch

from hermes.feishu.bot import EMOJI_DONE
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
        self.add_reaction_fn = MagicMock(return_value="rxn_done")
        self.remove_reaction_fn = MagicMock()
        self.worker = FeishuWorker(
            send_message=self.send_fn,
            reply_message=self.reply_fn,
            chat_fn=self.chat_fn,
            set_conversation=MagicMock(),
            add_reaction=self.add_reaction_fn,
            remove_reaction=self.remove_reaction_fn,
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

    # ---- 表情(处理中 ⏳ -> 完成 ✅)相关 ----

    def test_completed_group_task_updates_reactions(self):
        # 处理完成后:取消 ⏳(用记录的 reaction_id)+ 添加 ✅,然后回复
        task = {
            "open_id": "ou_abc",
            "text": "check disk",
            "chat_type": "group",
            "message_id": "om_123",
            "conversation_id": None,
            "reaction_id": "rxn_proc",
        }
        self.worker.process_task(task)
        # 删除「处理中」表情
        self.remove_reaction_fn.assert_called_once_with("om_123", "rxn_proc")
        # 添加「完成」表情
        self.add_reaction_fn.assert_called_once_with("om_123", EMOJI_DONE)
        self.reply_fn.assert_called_once_with("om_123", "ok, checked")

    def test_no_reaction_id_skips_remove_but_still_adds_done(self):
        # 没有 reaction_id(未成功加 ⏳)时:跳过删除,仍尝试加 ✅
        task = {
            "open_id": "ou_abc",
            "text": "hi",
            "chat_type": "p2p",
            "message_id": "om_123",
            "conversation_id": None,
            "reaction_id": None,
        }
        self.worker.process_task(task)
        self.remove_reaction_fn.assert_not_called()
        self.add_reaction_fn.assert_called_once_with("om_123", EMOJI_DONE)

    def test_without_message_id_skips_all_reactions(self):
        # 无 message_id 时完全跳过表情更新,仍正常回复
        task = {
            "open_id": "ou_abc",
            "text": "hi",
            "chat_type": "p2p",
            "message_id": None,
            "conversation_id": None,
            "reaction_id": "rxn_proc",
        }
        self.worker.process_task(task)
        self.remove_reaction_fn.assert_not_called()
        self.add_reaction_fn.assert_not_called()
        self.send_fn.assert_called_once_with("ou_abc", "ok, checked")

    def test_reaction_failures_do_not_block_reply(self):
        # 表情操作失败只记日志,不影响最终回复
        self.remove_reaction_fn.side_effect = RuntimeError("api down")
        self.add_reaction_fn.side_effect = RuntimeError("api down")
        task = {
            "open_id": "ou_abc",
            "text": "hi",
            "chat_type": "p2p",
            "message_id": "om_123",
            "conversation_id": None,
            "reaction_id": "rxn_proc",
        }
        with patch("hermes.feishu.tasks.logger"):
            self.worker.process_task(task)
        self.send_fn.assert_called_once_with("ou_abc", "ok, checked")


if __name__ == "__main__":
    unittest.main()
