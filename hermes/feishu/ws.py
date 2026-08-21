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

# 发送/回复消息的失败重试次数(规格:失败重试 2 次)
_RETRY_TIMES = 2


def _send_with_retry(fn) -> None:
    """执行发送,失败重试 _RETRY_TIMES 次后仍失败则记录日志。"""
    last_exc = None
    for attempt in range(_RETRY_TIMES + 1):
        try:
            fn()
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("飞书发送失败(第 %d/%d 次): %s",
                           attempt + 1, _RETRY_TIMES + 1, exc)
    logger.error("飞书发送重试 %d 次后仍失败: %s", _RETRY_TIMES, last_exc)


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
        )

        def _do_send() -> None:
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(lark.JSON.marshal({"text": text}))
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(body)
                .build()
            )
            resp = api_client.im.v1.message.create(req)
            if not resp.success():
                raise RuntimeError(f"code={resp.code} msg={resp.msg}")

        _send_with_retry(_do_send)

    def reply_message(message_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        def _do_reply() -> None:
            body = (
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(lark.JSON.marshal({"text": text}))
                .build()
            )
            req = ReplyMessageRequest.builder().message_id(message_id).body(body).build()
            resp = api_client.im.v1.message.reply(req)
            if not resp.success():
                raise RuntimeError(f"code={resp.code} msg={resp.msg}")

        _send_with_retry(_do_reply)

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


def _cleanup_for_tests():
    """仅供测试重置进程级单例。"""
    global _bot, _ws_client, _worker_thread
    _bot = None
    _ws_client = None
    _worker_thread = None
