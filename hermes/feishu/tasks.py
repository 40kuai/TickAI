"""飞书后台 worker — 消费队列,调用 chat(),发送回复.

不直接依赖 lark SDK。chat_fn 通过构造参数注入,测试时用 mock。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from hermes.agents.chat import chat as default_chat

logger = logging.getLogger(__name__)


class FeishuWorker:
    """消费 FeishuBot 队列中的任务。"""

    def __init__(
        self,
        send_message: Callable[[str, str], Any],
        reply_message: Callable[[str, str], Any],
        chat_fn: Callable[..., Dict[str, Any]] = default_chat,
        set_conversation: Callable[[str, int], None] = None,
    ) -> None:
        self.send_message = send_message
        self.reply_message = reply_message
        self.chat_fn = chat_fn
        self.set_conversation = set_conversation or (lambda open_id, cid: None)

    def process_task(self, task: Dict[str, Any]) -> None:
        """处理单条任务:执行 chat() 并按场景回复。"""
        open_id = task.get("open_id")
        text = task.get("text")
        chat_type = task.get("chat_type")
        message_id = task.get("message_id")
        conversation_id = task.get("conversation_id")

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

        if chat_type == "group" and message_id:
            self.reply_message(message_id, reply)
        else:
            self.send_message(open_id, reply)

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
