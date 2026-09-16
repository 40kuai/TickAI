"""飞书事件解析与白名单校验 — 纯函数,不依赖 lark SDK.

这些函数只接收普通 dict(模拟 SDK 事件的 JSON 结构),便于独立单测。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def extract_message_text(event: Dict[str, Any]) -> str:
    """从飞书消息事件中提取纯文本内容。

    事件结构(与 lark P2ImMessageReceiveV1 一致):
        {
          "sender": {"sender_id": {"open_id": "ou_xxx"}},
          "message": {
            "chat_type": "p2p" | "group",
            "message_type": "text" | ...,
            "content": '{"text":"..."}',
            "mentions": [...],
          }
        }
    """
    message = event.get("message") or {}
    if message.get("message_type") != "text":
        return ""
    content = message.get("content") or ""
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("text") or "")


def get_open_id(event: Dict[str, Any]) -> Optional[str]:
    """从事件中提取发送者 OpenID。"""
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    return sender_id.get("open_id")


def is_whitelisted(open_id: Optional[str], whitelist: List[str]) -> bool:
    """判断 OpenID 是否在白名单中。"""
    if not open_id:
        return False
    return open_id in whitelist


def should_respond(event: Dict[str, Any], whitelist: List[str]) -> bool:
    """综合判断:是否应该处理这条消息。

    规则:
      1. 发送者必须在白名单
      2. 消息必须是文本
      3. 单聊(p2p)直接响应;群聊(group)必须 @机器人 才响应
    """
    open_id = get_open_id(event)
    if not is_whitelisted(open_id, whitelist):
        return False

    message = event.get("message") or {}
    if message.get("message_type") != "text":
        return False

    chat_type = message.get("chat_type")
    if chat_type == "p2p":
        return True
    if chat_type == "group":
        # 群聊需确认 @了机器人(mentions 非空)
        mentions = message.get("mentions") or []
        return len(mentions) > 0
    return False
