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


if __name__ == "__main__":
    unittest.main()
