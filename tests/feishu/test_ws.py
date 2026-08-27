"""Tests for hermes.feishu.ws — startup wiring (SDK mocked)."""
import unittest
from unittest.mock import MagicMock, patch

import hermes.feishu.ws as ws_module
from hermes.feishu.ws import (
    _send_with_retry,
    _start_ws,
    build_event_handler,
    start_feishu_bot,
)


class SendWithRetryTests(unittest.TestCase):
    def test_retries_failing_function_two_times_then_gives_up(self):
        fn = MagicMock(side_effect=RuntimeError("boom"))
        with patch("hermes.feishu.ws.logger") as mock_logger:
            _send_with_retry(fn)
        # 1 次原始调用 + 重试 2 次 = 共 3 次尝试
        self.assertEqual(fn.call_count, 3)
        mock_logger.error.assert_called_once()

    def test_returns_immediately_on_success(self):
        fn = MagicMock()
        with patch("hermes.feishu.ws.logger"):
            _send_with_retry(fn)
        # 成功后只调用 1 次,不触发重试
        fn.assert_called_once()


class BuildEventHandlerTests(unittest.TestCase):
    def test_builds_handler_and_registers_event(self):
        bot = MagicMock()
        mock_lark = MagicMock()
        with patch("hermes.feishu.ws._import_lark", return_value=mock_lark):
            build_event_handler(bot)
            # 断言 builder 被调用且注册了消息事件
            mock_lark.EventDispatcherHandler.builder.assert_called_once()
            mock_builder = mock_lark.EventDispatcherHandler.builder.return_value
            mock_builder.register_p2_im_message_receive_v1.assert_called_once()


class StartWsTests(unittest.TestCase):
    """_start_ws:专用线程内首次导入 lark 并启动 ws client。"""

    def tearDown(self) -> None:
        ws_module._ws_client = None

    def test_starts_ws_client_with_config(self):
        bot = MagicMock()
        mock_lark = MagicMock()
        # client.start() 会阻塞,这里让它立即返回
        mock_lark.ws.Client.return_value.start.return_value = None
        with patch("hermes.feishu.ws._import_lark", return_value=mock_lark), \
             patch("hermes.feishu.ws.logger"):
            _start_ws("cli_x", "sec", bot)
        # 用传入的 app_id/app_secret 构造 ws client
        mock_lark.ws.Client.assert_called_once_with(
            "cli_x", "sec",
            event_handler=mock_lark.EventDispatcherHandler.builder.return_value
                .register_p2_im_message_receive_v1.return_value.build.return_value,
            log_level=mock_lark.LogLevel.DEBUG,
        )
        # 已启动
        mock_lark.ws.Client.return_value.start.assert_called_once()
        self.assertIsNotNone(ws_module._ws_client)

    def test_start_failure_logs_and_does_not_raise(self):
        bot = MagicMock()
        mock_lark = MagicMock()
        mock_lark.ws.Client.side_effect = RuntimeError("boom")
        with patch("hermes.feishu.ws._import_lark", return_value=mock_lark), \
             patch("hermes.feishu.ws.logger") as mock_logger:
            # 不应抛出异常
            _start_ws("cli_x", "sec", bot)
            mock_logger.error.assert_called_once()


class StartFeishuBotTests(unittest.TestCase):
    def setUp(self) -> None:
        # 重置模块级单例,避免用例间相互干扰
        ws_module._bot = None
        ws_module._ws_client = None
        ws_module._worker_thread = None

    def tearDown(self) -> None:
        ws_module._bot = None
        ws_module._ws_client = None
        ws_module._worker_thread = None

    def test_disabled_does_nothing(self):
        with patch("hermes.feishu.ws.config") as mock_config, \
             patch("hermes.feishu.ws.FeishuBot") as mock_bot_cls:
            mock_config.FEISHU_ENABLED.return_value = False
            start_feishu_bot()
            mock_bot_cls.assert_not_called()
            self.assertIsNone(ws_module._bot)

    def test_enabled_builds_bot_and_two_threads(self):
        with patch("hermes.feishu.ws.config") as mock_config, \
             patch("hermes.feishu.ws._import_lark") as mock_import_lark, \
             patch("hermes.feishu.ws.FeishuBot") as mock_bot_cls, \
             patch("hermes.feishu.ws.FeishuWorker") as mock_worker_cls, \
             patch("hermes.feishu.ws.threading.Thread") as mock_thread:
            mock_config.FEISHU_ENABLED.return_value = True
            mock_config.FEISHU_APP_ID.return_value = "cli_x"
            mock_config.FEISHU_APP_SECRET.return_value = "sec"
            mock_config.FEISHU_OPENID_WHITELIST.return_value = ["ou_abc"]
            start_feishu_bot()
            # bot 被构建
            mock_bot_cls.assert_called_once()
            # 拉起两个后台线程:worker 消费线程 + 长连接启动线程
            self.assertEqual(mock_thread.call_count, 2)
            self.assertTrue(mock_thread.call_args_list[0][1]["daemon"])
            self.assertTrue(mock_thread.call_args_list[1][1]["daemon"])
            # worker 线程以 run_forever 为 target,args 为 (bot,)
            worker_thread_kwargs = mock_thread.call_args_list[0][1]
            self.assertEqual(worker_thread_kwargs["target"],
                             mock_worker_cls.return_value.run_forever)
            self.assertEqual(worker_thread_kwargs["args"][0],
                             mock_bot_cls.return_value)
            # 长连接线程以 _start_ws 为 target
            ws_thread_kwargs = mock_thread.call_args_list[1][1]
            self.assertIs(ws_thread_kwargs["target"], _start_ws)
            # 本函数内不真正 import lark(import 交给 _start_ws 线程)
            mock_import_lark.assert_not_called()

    def test_second_call_is_idempotent_skips_restart(self):
        with patch("hermes.feishu.ws.config") as mock_config, \
             patch("hermes.feishu.ws._import_lark"), \
             patch("hermes.feishu.ws.FeishuBot") as mock_bot_cls, \
             patch("hermes.feishu.ws.FeishuWorker"), \
             patch("hermes.feishu.ws.threading.Thread") as mock_thread:
            mock_config.FEISHU_ENABLED.return_value = True
            mock_config.FEISHU_APP_ID.return_value = "cli_x"
            mock_config.FEISHU_APP_SECRET.return_value = "sec"
            mock_config.FEISHU_OPENID_WHITELIST.return_value = ["ou_abc"]
            # 第一次启动:正常构建
            start_feishu_bot()
            self.assertIsNotNone(ws_module._bot)
            self.assertEqual(mock_thread.call_count, 2)
            # 第二次启动:幂等保护,直接跳过,不重复构建/拉线程
            start_feishu_bot()
            mock_bot_cls.assert_called_once()
            self.assertEqual(mock_thread.call_count, 2)

    def test_stop_resets_module_globals(self):
        with patch("hermes.feishu.ws.config") as mock_config, \
             patch("hermes.feishu.ws._import_lark"), \
             patch("hermes.feishu.ws.FeishuBot") as mock_bot_cls, \
             patch("hermes.feishu.ws.FeishuWorker"), \
             patch("hermes.feishu.ws.threading.Thread") as mock_thread:
            mock_config.FEISHU_ENABLED.return_value = True
            mock_config.FEISHU_APP_ID.return_value = "cli_x"
            mock_config.FEISHU_APP_SECRET.return_value = "sec"
            mock_config.FEISHU_OPENID_WHITELIST.return_value = ["ou_abc"]
            start_feishu_bot()
            self.assertIsNotNone(ws_module._bot)
            ws_module.stop_feishu_bot()
            self.assertIsNone(ws_module._bot)
            self.assertIsNone(ws_module._ws_client)
            self.assertIsNone(ws_module._worker_thread)


if __name__ == "__main__":
    unittest.main()
