"""Tests for hermes.selfheal.actions (template whitelist + SSH executor)."""
import os
import unittest
from unittest.mock import patch

from hermes.selfheal import actions
from hermes.selfheal import config


class RenderCommandTests(unittest.TestCase):
    def setUp(self):
        os.environ["SELFHEAL_SERVICE_WHITELIST"] = "nginx,redis"
        os.environ["SELFHEAL_LOG_PATH_WHITELIST"] = "/var/log/nginx/access.log,/var/log/app"
        os.environ["SELFHEAL_DROPCACHES_MODES_WHITELIST"] = "1,2,3"
        config.reload_config()

    def test_restart_allowed_service(self):
        cmd = actions.render_command("restart_service", {"service": "nginx"})
        self.assertEqual(cmd, "systemctl restart nginx")

    def test_restart_rejects_non_whitelisted(self):
        with self.assertRaises(ValueError):
            actions.render_command("restart_service", {"service": "evil"})

    def test_truncate_allowed_path(self):
        cmd = actions.render_command("truncate_log", {"path": "/var/log/nginx/access.log"})
        self.assertEqual(cmd, "truncate -s 0 /var/log/nginx/access.log")

    def test_truncate_rejects_outside_whitelist(self):
        with self.assertRaises(ValueError):
            actions.render_command("truncate_log", {"path": "/etc/passwd"})

    def test_clean_cache_allowed_mode(self):
        cmd = actions.render_command("clean_cache", {"mode": "3"})
        self.assertEqual(cmd, "sync && echo 3 > /proc/sys/vm/drop_caches")

    def test_clean_cache_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            actions.render_command("clean_cache", {"mode": "9"})

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            actions.render_command("nope", {})


class ExecSshTests(unittest.TestCase):
    def test_success(self):
        with patch("hermes.selfheal.actions.get_server_ssh_args",
                   return_value=("10.0.0.1", {"port": 22, "username": "root",
                                              "password": "x"}, "web-01")), \
             patch("hermes.selfheal.actions._connect_exec",
                   return_value={"success": True, "exit_code": 0,
                                 "stdout": "active", "stderr": ""}):
            r = actions.exec_ssh(1, "systemctl is-active nginx")
        self.assertTrue(r["success"])
        self.assertEqual(r["stdout"], "active")

    def test_ssh_error(self):
        with patch("hermes.selfheal.actions.get_server_ssh_args",
                   return_value=("10.0.0.1", {"port": 22, "username": "root",
                                              "password": "x"}, "web-01")), \
             patch("hermes.selfheal.actions._connect_exec",
                   side_effect=Exception("connection refused")):
            r = actions.exec_ssh(1, "systemctl is-active nginx")
        self.assertFalse(r["success"])
        self.assertIn("error", r)

    def test_ssh_invalid_port(self):
        # DB 脏数据端口越界 → 返回结构化失败而不是依赖异常收敛
        with patch("hermes.selfheal.actions.get_server_ssh_args",
                   return_value=("10.0.0.1", {"port": 99999, "username": "root",
                                              "password": "x"}, "web-01")), \
             patch("hermes.selfheal.actions._connect_exec") as m:
            r = actions.exec_ssh(1, "systemctl is-active nginx")
        self.assertFalse(r["success"])
        self.assertIn("invalid SSH port", r["error"])
        m.assert_not_called()

    def test_connect_exec_invalid_key(self):
        # 密钥内容非法 → 返回 {success: False, error}
        r = actions._connect_exec("10.0.0.1", 22, "root", "", "not-a-valid-key",
                                  "systemctl is-active nginx")
        self.assertFalse(r["success"])
        self.assertIn("error", r)

    def test_connect_exec_connection_error(self):
        # 连接异常 → 收敛为结构化失败
        with patch("paramiko.SSHClient") as m:
            m.return_value.connect.side_effect = Exception("connection refused")
            r = actions._connect_exec("10.0.0.1", 22, "root", "x", "",
                                      "systemctl is-active nginx")
        self.assertFalse(r["success"])
        self.assertIn("error", r)

    def test_connect_exec_success(self):
        # 命令成功 → 返回 stdout/exit_code
        fake_stdout = unittest.mock.MagicMock()
        fake_stdout.read.return_value = b"active"
        fake_stdout.channel.recv_exit_status.return_value = 0
        fake_stderr = unittest.mock.MagicMock()
        fake_stderr.read.return_value = b""
        with patch("paramiko.SSHClient") as m:
            client = m.return_value
            client.exec_command.return_value = (None, fake_stdout, fake_stderr)
            r = actions._connect_exec("10.0.0.1", 22, "root", "x", "",
                                      "systemctl is-active nginx")
        self.assertTrue(r["success"])
        self.assertEqual(r["stdout"], "active")
        self.assertEqual(r["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
