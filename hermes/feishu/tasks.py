"""飞书后台 worker — 消费队列,调用 chat(),发送回复.

不直接依赖 lark SDK。chat_fn 通过构造参数注入,测试时用 mock。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from hermes.agents.chat import chat as default_chat

from .bot import EMOJI_DONE

logger = logging.getLogger(__name__)


class FeishuWorker:
    """消费 FeishuBot 队列中的任务。"""

    def __init__(
        self,
        send_message: Callable[[str, str], Any],
        reply_message: Callable[[str, str], Any],
        chat_fn: Callable[..., Dict[str, Any]] = default_chat,
        set_conversation: Callable[[str, int], None] = None,
        add_reaction: Callable[[str, str], Any] = None,
        remove_reaction: Callable[[str, str], Any] = None,
    ) -> None:
        self.send_message = send_message
        self.reply_message = reply_message
        self.chat_fn = chat_fn
        self.set_conversation = set_conversation or (lambda open_id, cid: None)
        self.add_reaction = add_reaction  # (message_id, emoji) -> reaction_id
        self.remove_reaction = remove_reaction  # (message_id, reaction_id)

    def process_task(self, task: Dict[str, Any]) -> None:
        """处理单条任务:执行 chat() 并按场景回复。"""
        open_id = task.get("open_id")
        text = task.get("text")
        chat_type = task.get("chat_type")
        message_id = task.get("message_id")
        conversation_id = task.get("conversation_id")
        reaction_id = task.get("reaction_id")  # bot 加的「处理中」表情

        try:
            result = self.chat_fn(text, conversation_id=conversation_id)
            reply = result.get("reply") or ""
            new_conv_id = result.get("conversation_id")
        except Exception as exc:  # noqa: BLE001
            logger.error("飞书任务执行失败: %s", exc)
            reply = f"处理错误: {exc}"
            new_conv_id = None

        if new_conv_id:
            self.set_conversation(open_id, new_conv_id)

        # 处理完成:取消「处理中」表情,并在用户消息上加「完成」表情。
        self._update_reaction_status(message_id, reaction_id)

        if chat_type == "group" and message_id:
            self.reply_message(message_id, reply)
        else:
            self.send_message(open_id, reply)

    def _update_reaction_status(self, message_id, reaction_id) -> None:
        """处理完成后更新消息表情:删除 ⏳,添加 ✅。失败只记日志,不阻断回复。"""
        if not message_id:
            return
        # 删除「处理中」表情(需要之前记录的 reaction_id)
        if reaction_id and self.remove_reaction is not None:
            try:
                self.remove_reaction(message_id, reaction_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("取消「处理中」表情失败: %s", exc)
        # 添加「完成」表情
        if self.add_reaction is not None:
            try:
                self.add_reaction(message_id, EMOJI_DONE)
            except Exception as exc:  # noqa: BLE001
                logger.warning("添加「完成」表情失败: %s", exc)

    def run_forever(self, bot) -> None:
        """阻塞循环:持续从 bot 队列取任务处理。供线程启动。"""
        while True:
            task = bot.queue.get()
            try:
                self.process_task(task)
            except Exception as exc:  # noqa: BLE001
                logger.error("处理飞书任务异常: %s", exc)
            finally:
                bot.queue.task_done()
