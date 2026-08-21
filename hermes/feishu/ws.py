"""飞书长连接客户端 — 启动 WebSocket、注册事件、拉起 worker 线程.

使用官方 SDK 的长连接(WebSocket)模式,无需公网回调地址。

重要:lark_oapi 的 ws 客户端在模块导入时用 asyncio.get_event_loop() 捕获
事件循环,若在 uvicorn 主循环(uvloop)运行时导入,会绑定到正在运行的
主循环,导致 ws.Client.start() 抛出 "this event loop is already running"。
因此本模块不在此处 import lark_oapi,而是通过 _import_lark() 延迟导入,
并保证首次导入发生在无运行中事件循环的专用线程中。
"""
from __future__ import annotations

import logging
import threading

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


def _import_lark():
    """延迟导入 lark_oapi(必须在无运行中事件循环的线程中首次调用).

    lark_oapi 会连带加载 lark_oapi.ws.client,该模块在导入时用
    asyncio.get_event_loop() 捕获事件循环并全局固定。若在 uvicorn
    主循环运行时导入,会绑定到正在运行的 uvloop 主循环,后续
    ws.Client.start() 将抛 "this event loop is already running"。
    因此在干净的专用线程中首次导入,使其绑定到专属的新循环。
    """
    import lark_oapi  # noqa: PLC0415
    return lark_oapi


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
    lark = _import_lark()
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
    """启动飞书长连接(按配置)。未启用时静默跳过。

    lark 的导入与 ws 启动被放进专用的 _start_ws 线程,确保首次
    import lark_oapi 时该线程没有运行中的事件循环,避免与 uvicorn
    主循环(uvloop)冲突。
    """
    global _bot, _ws_client, _worker_thread
    if not config.FEISHU_ENABLED():
        logger.info("飞书未配置,跳过启动")
        return

    if _bot is not None:
        logger.info("飞书 bot 已启动,跳过重复启动")
        return

    app_id = config.FEISHU_APP_ID()
    app_secret = config.FEISHU_APP_SECRET()
    whitelist = config.FEISHU_OPENID_WHITELIST()

    # worker 需要发送/回复函数,而这些函数在 ws 线程首次 import lark 后才
    # 可用。这里先建一个"延迟初始化"的发送器,内部通过 _import_lark() 获取。
    api_client_holder = {"client": None}

    def _get_api_client():
        if api_client_holder["client"] is None:
            lark = _import_lark()
            api_client_holder["client"] = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .build()
            )
        return api_client_holder["client"]

    def send_message(open_id: str, text: str) -> None:
        lark = _import_lark()
        from lark_oapi.api.im.v1 import (  # noqa: PLC0415
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
            resp = _get_api_client().im.v1.message.create(req)
            if not resp.success():
                raise RuntimeError(f"code={resp.code} msg={resp.msg}")

        _send_with_retry(_do_send)

    def reply_message(message_id: str, text: str) -> None:
        lark = _import_lark()
        from lark_oapi.api.im.v1 import (  # noqa: PLC0415
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
            req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
            resp = _get_api_client().im.v1.message.reply(req)
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

    # 长连接启动线程(阻塞,daemon)。此线程内首次 import lark,
    # 保证 lark 模块级事件循环绑定到本线程专属的新循环,不与主循环冲突。
    threading.Thread(
        target=_start_ws,
        args=(app_id, app_secret, _bot),
        daemon=True,
    ).start()


def _start_ws(app_id: str, app_secret: str, bot: FeishuBot) -> None:
    """在专用线程中启动飞书长连接(阻塞运行).

    必须由独立线程调用:此函数内部首次 import lark_oapi,确保 lark 的
    模块级事件循环绑定到本线程专属的新循环,避免与 uvicorn 主循环冲突。
    """
    global _ws_client
    try:
        lark = _import_lark()
        event_handler = build_event_handler(bot)
        client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        _ws_client = client
        logger.info("飞书长连接已启动")
        client.start()  # 阻塞:运行事件循环直至连接断开
    except Exception as exc:  # noqa: BLE001
        logger.error("飞书长连接启动失败: %s", exc)


def stop_feishu_bot() -> None:
    """关闭飞书连接(供进程退出时调用)。"""
    global _bot, _ws_client, _worker_thread
    if _ws_client is not None:
        # lark 1.7.3 的 ws.Client 无优雅关闭接口(仅 start),其所在线程为 daemon,
        # 随进程退出自动结束,这里仅释放引用避免误用已停止的客户端。
        logger.info("飞书长连接正在停止,释放 ws 客户端引用")
        _ws_client = None
    _bot = None
    _worker_thread = None
