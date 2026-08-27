"""飞书消息分发器 — 白名单校验 + 异步队列 + 会话映射.

不直接依赖 lark SDK。发送消息/回复消息通过构造参数注入,
便于测试时用 mock 替换真实 API 调用。
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Dict, Optional

from .handler import extract_message_text, should_respond

logger = logging.getLogger(__name__)


class FeishuBot:
    """接收飞书消息,校验后入队,并立即回复「处理中」。"""

    def __init__(
        self,
        whitelist: list,
        send_message: Callable[[str, str], Any],
        reply_message: Callable[[str, str], Any],
    ) -> None:
        self.whitelist = list(whitelist)
        self.send_message = send_message  # (open_id, text) 单聊发送
        self.reply_message = reply_message  # (message_id, text) 群聊回复
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._conversation_map: Dict[str, int] = {}  # open_id -> conversation_id
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 事件入口(由 ws.py 的事件回调调用)
    # ------------------------------------------------------------------
    def handle_event(self, event: Dict[str, Any]) -> None:
        """处理一条飞书消息事件。立即回复「处理中」并入队。"""
        # 调试用:无论是否放行,都记录发送者 open_id,便于首次配置白名单时获取。
        sender_open_id = (event.get("sender") or {}).get("sender_id", {}).get("open_id")
        logger.info("收到飞书消息: open_id=%s", sender_open_id)

        if not should_respond(event, self.whitelist):
            return

        text = extract_message_text(event)
        if not text.strip():
            return

        message = event.get("message") or {}
        chat_type = message.get("chat_type")
        message_id = message.get("message_id")
        open_id = (event.get("sender") or {}).get("sender_id", {}).get("open_id")

        # 立即回复「处理中」(群聊回复原消息,单聊直接发送)。
        # 若失败只记录日志,不阻断后续入队,保证任务仍会被处理。
        if chat_type == "group" and message_id:
            try:
                self.reply_message(message_id, "正在处理,请稍候…")
            except Exception as exc:  # noqa: BLE001
                logger.warning("即时回复(群聊)失败: %s", exc)
        else:
            if not open_id:
                logger.warning("缺少 open_id,无法发送「处理中」,任务仍入队")
            else:
                try:
                    self.send_message(open_id, "正在处理,请稍候…")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("即时回复(单聊)失败: %s", exc)

        # 构造任务并入队
        task = self._build_task(event, text)
        self.queue.put(task)
        logger.info("飞书消息已入队: open_id=%s", open_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _build_task(self, event: Dict[str, Any], text: str) -> Dict[str, Any]:
        """构造队列任务。群聊带 message_id(用 reply 回复),单聊带 open_id。"""
        message = event.get("message") or {}
        chat_type = message.get("chat_type")
        open_id = (event.get("sender") or {}).get("sender_id", {}).get("open_id")

        task = {
            "open_id": open_id,
            "text": text,
            "chat_type": chat_type,
            "message_id": message.get("message_id"),
            "conversation_id": self.get_conversation(open_id),
        }
        return task

    def get_conversation(self, open_id: str) -> Optional[int]:
        """读取该用户当前会话 ID(无则 None,chat() 会新建会话)。"""
        with self._lock:
            return self._conversation_map.get(open_id)

    def set_conversation(self, open_id: str, conversation_id: int) -> None:
        """记录该用户当前会话 ID(由 worker 在 chat() 完成后写入)。"""
        with self._lock:
            self._conversation_map[open_id] = conversation_id
