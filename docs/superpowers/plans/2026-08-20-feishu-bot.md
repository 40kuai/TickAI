# Feishu Bot — 飞书机器人远程运维接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户通过与飞书机器人对话,远程调用 TickAI 的运维能力(磁盘检查、资源检查、服务列表等)。

**Architecture:** 复用现有 FastAPI 进程与 `hermes.agents.chat.chat()` 对话引擎。新增 `hermes/feishu/` 模块:飞书长连接(WebSocket)接收消息 → 白名单校验 → 异步队列 → 后台线程执行 `chat()` → 通过飞书消息 API 回复。事件回调因飞书要求 3 秒内返回,必须立即回复「处理中」并入队异步处理。

**Tech Stack:** Python 3.9、lark-oapi (>=1.5,<2,兼容 Python 3.7+)、FastAPI、unittest

**Spec:** [2026-08-20-feishu-bot-design.md](../specs/2026-08-20-feishu-bot-design.md)

---

## File Structure

| 文件 | 职责 |
|---|---|
| `hermes/feishu/__init__.py` | 空包,导出模块 |
| `hermes/feishu/handler.py` | 解析飞书事件、白名单校验、@机器人判断(纯函数,可单测) |
| `hermes/feishu/bot.py` | 消息分发器:队列、会话映射、回复入口(依赖 lark SDK) |
| `hermes/feishu/tasks.py` | 后台 worker:消费队列,调 `chat()`,发送回复 |
| `hermes/feishu/ws.py` | 长连接客户端:启动/事件注册(依赖 lark SDK) |
| `hermes/config/settings.py` | 新增 FEISHU_* 配置读取 |
| `api/main.py` | startup 时按配置启动飞书长连接 |
| `.env.example` | 新增飞书配置模板 |
| `requirements.txt` | 新增 lark-oapi |
| `tests/feishu/__init__.py` | 空包 |
| `tests/feishu/test_handler.py` | handler 纯函数测试 |
| `tests/feishu/test_bot.py` | bot 队列/分发测试(不依赖真实 SDK) |
| `tests/feishu/test_tasks.py` | worker 处理测试(不依赖真实 SDK) |

> **重要设计约束:** `handler.py` 中的解析/校验逻辑必须是纯函数(不 import lark_oapi),这样 `test_handler.py` 不依赖 SDK 即可单测。`bot.py`、`tasks.py`、`ws.py` 通过注入方式接收"发送消息"函数和"chat 函数",测试时注入 mock,避免真实调用飞书 API。

---

### Task 1: 添加 lark-oapi 依赖并更新配置

**Files:**
- Modify: `requirements.txt`
- Modify: `hermes/config/settings.py`
- Modify: `.env.example`

- [ ] **Step 1: 在 requirements.txt 添加依赖**

在 `requirements.txt` 末尾追加:

```
# 飞书 SDK
lark-oapi>=1.5.0,<2.0.0
```

- [ ] **Step 2: 在 settings.py 添加飞书配置函数**

在 `hermes/config/settings.py` 末尾(第 126 行 `LDAP_CONFIGURED` 之后)追加:

```python
# 飞书 — 运行时读取
def FEISHU_APP_ID() -> str:
    return get("FEISHU_APP_ID", "")


def FEISHU_APP_SECRET() -> str:
    return get("FEISHU_APP_SECRET", "")


def FEISHU_OPENID_WHITELIST() -> list:
    raw = get("FEISHU_OPENID_WHITELIST", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def FEISHU_ENABLED() -> bool:
    """检查飞书是否已配置"""
    return bool(FEISHU_APP_ID() and FEISHU_APP_SECRET())
```

- [ ] **Step 3: 在 .env.example 添加飞书配置模板**

在 `.env.example` 末尾追加:

```
# ======================================
# 💬 Feishu Bot - Optional
# ======================================
# 企业自建应用凭据(开放平台「凭证与基础信息」页)
# FEISHU_APP_ID=cli_xxx
# FEISHU_APP_SECRET=xxx
# 允许使用机器人的用户 OpenID(逗号分隔)。留空则无人可用。
# FEISHU_OPENID_WHITELIST=ou_xxx,ou_yyy
```

- [ ] **Step 4: 验证配置函数工作**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -c "from hermes.config import settings as s; print(s.FEISHU_ENABLED()); print(s.FEISHU_OPENID_WHITELIST())"`
Expected: `False`(未配置时)和 `[]`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt hermes/config/settings.py .env.example
git commit -m "feat: 添加飞书配置与 lark-oapi 依赖"
```

---

### Task 2: 实现事件解析与白名单校验(纯函数)

**Files:**
- Create: `hermes/feishu/__init__.py`
- Create: `hermes/feishu/handler.py`
- Test: `tests/feishu/__init__.py`
- Test: `tests/feishu/test_handler.py`

- [ ] **Step 1: 创建包结构**

创建 `hermes/feishu/__init__.py`(空文件)和 `tests/feishu/__init__.py`(空文件)。

- [ ] **Step 2: 写失败测试**

创建 `tests/feishu/test_handler.py`:

```python
"""Tests for hermes.feishu.handler — pure parsing/whitelist logic."""
import unittest

from hermes.feishu.handler import (
    extract_message_text,
    is_whitelisted,
    should_respond,
)


# 模拟 lark P2ImMessageReceiveV1 结构的最小 dict
def _make_event(open_id="ou_abc", chat_type="p2p", message_type="text",
                content='{"text":"check disk"}', mentions=None):
    return {
        "sender": {"sender_id": {"open_id": open_id}},
        "message": {
            "chat_type": chat_type,
            "message_type": message_type,
            "content": content,
            "mentions": mentions or [],
        },
    }


class ExtractMessageTextTests(unittest.TestCase):
    def test_text_message(self):
        event = _make_event(content='{"text":"hello"}')
        self.assertEqual(extract_message_text(event), "hello")

    def test_invalid_json_returns_empty(self):
        event = _make_event(content="not-json")
        self.assertEqual(extract_message_text(event), "")

    def test_non_text_message_returns_empty(self):
        event = _make_event(message_type="image", content='{"image_key":"x"}')
        self.assertEqual(extract_message_text(event), "")


class IsWhitelistedTests(unittest.TestCase):
    def test_in_whitelist(self):
        self.assertTrue(is_whitelisted("ou_abc", ["ou_abc", "ou_xyz"]))

    def test_not_in_whitelist(self):
        self.assertFalse(is_whitelisted("ou_other", ["ou_abc"]))

    def test_empty_whitelist(self):
        self.assertFalse(is_whitelisted("ou_abc", []))


class ShouldRespondTests(unittest.TestCase):
    def test_p2p_always_respond(self):
        event = _make_event(chat_type="p2p")
        self.assertTrue(should_respond(event, whitelist=["ou_abc"]))

    def test_group_with_mention_responds(self):
        event = _make_event(chat_type="group", mentions=[{"key": "123", "id": {"open_id": "ou_bot"}}])
        self.assertTrue(should_respond(event, whitelist=["ou_abc"]))

    def test_group_without_mention_ignored(self):
        event = _make_event(chat_type="group", mentions=[])
        self.assertFalse(should_respond(event, whitelist=["ou_abc"]))

    def test_non_whitelisted_ignored(self):
        event = _make_event(open_id="ou_hacker", chat_type="p2p")
        self.assertFalse(should_respond(event, whitelist=["ou_abc"]))

    def test_non_text_ignored(self):
        event = _make_event(message_type="image")
        self.assertFalse(should_respond(event, whitelist=["ou_abc"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_handler.py -v 2>&1 | tail -20`
Expected: FAIL(ModuleNotFoundError: hermes.feishu.handler)

- [ ] **Step 4: 实现 handler.py**

创建 `hermes/feishu/handler.py`:

```python
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_handler.py -v 2>&1 | tail -10`
Expected: PASS(10 passed)

- [ ] **Step 6: Commit**

```bash
git add hermes/feishu/ tests/feishu/
git commit -m "feat: 飞书事件解析与白名单校验"
```

---

### Task 3: 实现消息分发器(队列 + 会话映射)

**Files:**
- Create: `hermes/feishu/bot.py`
- Test: `tests/feishu/test_bot.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/feishu/test_bot.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_bot.py -v 2>&1 | tail -10`
Expected: FAIL(ModuleNotFoundError: hermes.feishu.bot)

- [ ] **Step 3: 实现 bot.py**

创建 `hermes/feishu/bot.py`:

```python
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
        if not should_respond(event, self.whitelist):
            return

        text = extract_message_text(event)
        if not text.strip():
            return

        message = event.get("message") or {}
        chat_type = message.get("chat_type")
        message_id = message.get("message_id")
        open_id = (event.get("sender") or {}).get("sender_id", {}).get("open_id")

        # 立即回复「处理中」(群聊回复原消息,单聊直接发送)
        if chat_type == "group" and message_id:
            self.reply_message(message_id, "正在处理,请稍候…")
        else:
            self.send_message(open_id, "正在处理,请稍候…")

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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_bot.py -v 2>&1 | tail -10`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add hermes/feishu/bot.py tests/feishu/test_bot.py
git commit -m "feat: 飞书消息分发器(队列+会话映射)"
```

---

### Task 4: 实现后台 worker(调 chat() 并发送回复)

**Files:**
- Create: `hermes/feishu/tasks.py`
- Test: `tests/feishu/test_tasks.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/feishu/test_tasks.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_tasks.py -v 2>&1 | tail -10`
Expected: FAIL(ModuleNotFoundError: hermes.feishu.tasks)

- [ ] **Step 3: 实现 tasks.py**

创建 `hermes/feishu/tasks.py`:

```python
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
            reply = f"处理出错: {exc}"
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_tasks.py -v 2>&1 | tail -10`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add hermes/feishu/tasks.py tests/feishu/test_tasks.py
git commit -m "feat: 飞书后台 worker 执行对话并回复"
```

---

### Task 5: 实现长连接客户端与启动接入

**Files:**
- Create: `hermes/feishu/ws.py`
- Modify: `api/main.py`
- Test: `tests/feishu/test_ws.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/feishu/test_ws.py`:

```python
"""Tests for hermes.feishu.ws — startup wiring (SDK mocked)."""
import unittest
from unittest.mock import MagicMock, patch

from hermes.feishu.ws import build_event_handler, start_feishu_bot


class BuildEventHandlerTests(unittest.TestCase):
    def test_builds_handler_and_registers_event(self):
        bot = MagicMock()
        with patch("hermes.feishu.ws.lark") as mock_lark:
            handler = build_event_handler(bot)
            # 断言 builder 被调用且注册了消息事件
            mock_lark.EventDispatcherHandler.builder.assert_called_once()
            mock_builder = mock_lark.EventDispatcherHandler.builder.return_value
            mock_builder.register_p2_im_message_receive_v1.assert_called_once()


class StartFeishuBotTests(unittest.TestCase):
    def test_disabled_does_nothing(self):
        with patch("hermes.feishu.ws.config") as mock_config, \
             patch("hermes.feishu.ws.FeishuBot") as mock_bot_cls:
            mock_config.FEISHU_ENABLED.return_value = False
            start_feishu_bot()
            mock_bot_cls.assert_not_called()

    def test_enabled_builds_bot_and_worker_thread(self):
        with patch("hermes.feishu.ws.config") as mock_config, \
             patch("hermes.feishu.ws.FeishuBot") as mock_bot_cls, \
             patch("hermes.feishu.ws.FeishuWorker") as mock_worker_cls, \
             patch("hermes.feishu.ws.threading.Thread") as mock_thread:
            mock_config.FEISHU_ENABLED.return_value = True
            mock_config.FEISHU_APP_ID.return_value = "cli_x"
            mock_config.FEISHU_APP_SECRET.return_value = "sec"
            mock_config.FEISHU_OPENID_WHITELIST.return_value = ["ou_abc"]
            start_feishu_bot()
            mock_bot_cls.assert_called_once()
            mock_thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_ws.py -v 2>&1 | tail -10`
Expected: FAIL(ModuleNotFoundError: hermes.feishu.ws)

- [ ] **Step 3: 实现 ws.py**

创建 `hermes/feishu/ws.py`:

```python
"""飞书长连接客户端 — 启动 WebSocket、注册事件、拉起 worker 线程.

使用官方 SDK 的长连接(WebSocket)模式,无需公网回调地址。
"""
from __future__ import annotations

import logging
import threading

import lark_oapi as lark

from hermes.config import settings as config

from .bot import FeishuBot
from .tasks import FeishuWorker

logger = logging.getLogger(__name__)

# 进程级单例,便于测试与关闭
_bot: FeishuBot = None
_ws_client = None
_worker_thread: threading.Thread = None


def build_event_handler(bot: FeishuBot):
    """构造事件分发器,注册接收消息事件。"""
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(
            lambda data: _on_message(bot, data)
        )
        .build()
    )
    return handler


def _on_message(bot: FeishuBot, data) -> None:
    """事件回调:把 lark 事件转成 dict 交给 bot 分发。"""
    event = data.event
    event_dict = {
        "sender": {
            "sender_id": {
                "open_id": event.sender.sender_id.open_id
                if event.sender and event.sender.sender_id else None
            }
        },
        "message": {
            "chat_type": event.message.chat_type if event.message else None,
            "message_type": event.message.message_type if event.message else None,
            "content": event.message.content if event.message else None,
            "message_id": event.message.message_id if event.message else None,
            "mentions": (
                [{"key": m.key, "id": {"open_id": m.id.open_id}}
                 for m in (event.message.mentions or [])]
                if event.message else []
            ),
        },
    }
    bot.handle_event(event_dict)


def start_feishu_bot() -> None:
    """启动飞书长连接(按配置)。未启用时静默跳过。"""
    global _bot, _ws_client, _worker_thread
    if not config.FEISHU_ENABLED():
        logger.info("飞书未配置,跳过启动")
        return

    app_id = config.FEISHU_APP_ID()
    app_secret = config.FEISHU_APP_SECRET()
    whitelist = config.FEISHU_OPENID_WHITELIST()

    # 注入真实的飞书消息发送/回复函数(通过 SDK 客户端)
    api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

    def send_message(open_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            CreateMessageRequestBodyReceiveIdType,
        )
        body = (
            CreateMessageRequestBody.builder()
            .receive_id_type(CreateMessageRequestBodyReceiveIdType.OPEN_ID)
            .receive_id(open_id)
            .msg_type("text")
            .content(lark.JSON.marshal({"text": text}))
            .build()
        )
        req = CreateMessageRequest.builder().body(body).build()
        resp = api_client.im.v1.message.create(req)
        if not resp.success():
            logger.error("飞书发送失败: %s", resp)

    def reply_message(message_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(lark.JSON.marshal({"text": text}))
            .build()
        )
        req = ReplyMessageRequest.builder().message_id(message_id).body(body).build()
        resp = api_client.im.v1.message.reply(req)
        if not resp.success():
            logger.error("飞书回复失败: %s", resp)

    _bot = FeishuBot(whitelist=whitelist,
                     send_message=send_message,
                     reply_message=reply_message)
    _worker = FeishuWorker(send_message=send_message,
                           reply_message=reply_message,
                           set_conversation=_bot.set_conversation)

    # worker 线程持续消费队列
    _worker_thread = threading.Thread(
        target=_worker.run_forever, args=(_bot,), daemon=True
    )
    _worker_thread.start()

    # 长连接客户端(阻塞,放后台线程)
    event_handler = build_event_handler(_bot)
    _ws_client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    threading.Thread(target=_ws_client.start, daemon=True).start()
    logger.info("飞书长连接已启动")


def stop_feishu_bot() -> None:
    """关闭飞书连接(供进程退出时调用)。"""
    global _ws_client
    if _ws_client is not None:
        try:
            _ws_client.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("停止飞书连接异常: %s", exc)
        _ws_client = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/feishu/test_ws.py -v 2>&1 | tail -10`
Expected: PASS(2 passed)

- [ ] **Step 5: 在 api/main.py 接入启动逻辑**

修改 `api/main.py`:

```python
from .ssh_credential_routes import router as ssh_cred_router
from .tool_routes import router as tool_router
```

之后追加 import:

```python
from hermes.feishu.ws import start_feishu_bot, stop_feishu_bot
```

再修改 startup 事件(第 56-59 行):

```python
@app.on_event("startup")
def startup() -> None:
    init_db()
    init_default_user()
    start_feishu_bot()
```

并追加 shutdown 事件(在 startup 之后):

```python
@app.on_event("shutdown")
def shutdown() -> None:
    stop_feishu_bot()
```

- [ ] **Step 6: 验证模块可导入(语法检查)**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -c "import ast; ast.parse(open('hermes/feishu/ws.py').read()); ast.parse(open('api/main.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 7: Commit**

```bash
git add hermes/feishu/ws.py api/main.py tests/feishu/test_ws.py
git commit -m "feat: 飞书长连接客户端与启动接入"
```

---

### Task 6: 安装依赖并运行全部测试

**Files:**
- None (只运行命令)

- [ ] **Step 1: 安装 lark-oapi(用国内镜像加速)**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/pip install "lark-oapi>=1.5.0,<2.0.0" -i https://pypi.tuna.tsinghua.edu.cn/simple`
Expected: Successfully installed lark-oapi

- [ ] **Step 2: 运行全部测试**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -m pytest tests/ -v 2>&1 | tail -30`
Expected: 全部 PASS(含既有测试与新增 feishu 测试)

- [ ] **Step 3: 验证飞书配置函数与包导入**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/python -c "import lark_oapi; from hermes.feishu import ws, bot, tasks, handler; print('feishu ok')"`
Expected: `feishu ok`

- [ ] **Step 4: Commit(若无新变更则跳过)**

```bash
git status --short
git commit -am "chore: 安装 lark-oapi 并验证测试"
```

---

### Task 7: 端到端冒烟验证(手动)

**Files:**
- None

- [ ] **Step 1: 填入真实飞书配置**

将 `.env` 中添加:

```
FEISHU_APP_ID=<your_feishu_app_id>
FEISHU_APP_SECRET=<your_feishu_app_secret>
FEISHU_OPENID_WHITELIST=<你的飞书 OpenID>
```

> ⚠️ 注意:`.env` 已被 .gitignore 忽略,不会被提交。

- [ ] **Step 2: 启动后端**

Run: `cd /Users/40kuai/Documents/ai && .venv/bin/uvicorn api.main:app --reload --port 8000`
Expected: 日志出现 `飞书长连接已启动`,无异常

- [ ] **Step 3: 在飞书单聊给机器人发消息**

预期:收到「正在处理,请稍候…」,随后收到 LLM 回复(如"你好,我是 TickAI…")

- [ ] **Step 4: 在群聊 @机器人 发消息**

预期:同上,且回复在原消息下

- [ ] **Step 5: 验证白名单外用户**

用非白名单账号发消息,预期:无任何回复

---

## Self-Review

**1. Spec coverage:**
- ✅ 长连接 WebSocket → Task 5
- ✅ 白名单 → Task 2 + Task 3
- ✅ 复用 chat() → Task 4(worker 注入 chat_fn,默认 hermes.agents.chat.chat)
- ✅ 异步队列(先回处理中) → Task 3
- ✅ 单聊/群聊 @ → Task 2 + Task 3
- ✅ 错误处理 → Task 4(chat 异常转错误回复)
- ✅ 配置 + .env.example → Task 1
- ✅ 依赖 lark-oapi → Task 1 + Task 6
- ✅ 启动接入 → Task 5

**2. Placeholder scan:** 无 TBD/TODO;每个代码步骤都有完整实现。

**3. Type consistency:** `FeishuBot`(queue/handle_event/_build_task/get_conversation/set_conversation)在 Task 3 定义、Task 4 的 `run_forever` 消费其 `queue`、Task 5 使用一致。`send_message(open_id, text)` / `reply_message(message_id, text)` 签名在 Task 3/4/5 保持一致。`chat_fn(text, conversation_id=...)` 与 `hermes.agents.chat.chat` 签名一致。
