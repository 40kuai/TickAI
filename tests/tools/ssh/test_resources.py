"""Tests for tools.system (check_resources, list_services SSH handlers)."""
import json
import unittest
from unittest.mock import MagicMock, patch

from hermes.tools.ssh.resources import (
    check_resources_handler,
    list_services_handler,
    parse_loadavg,
    parse_meminfo,
    parse_top_processes,
    parse_services_output,
    compute_pressure_level,
    RESOURCES_SCHEMA,
    SERVICES_SCHEMA,
)


class ParseLoadavgTests(unittest.TestCase):
    def test_parses_three_numbers(self):
        text = " 14:32:01 up 23 days, 1 user, load average: 0.42, 0.38, 0.41\n"
        out = parse_loadavg(text)
        self.assertEqual(out, [0.42, 0.38, 0.41])

    def test_handles_no_newline(self):
        text = " 14:32:01 up 1 day, load average: 1.00, 2.00, 3.00"
        out = parse_loadavg(text)
        self.assertEqual(out, [1.0, 2.0, 3.0])

    def test_returns_zeros_when_no_match(self):
        out = parse_loadavg("garbage input with no load average")
        self.assertEqual(out, [0.0, 0.0, 0.0])


class ParseMeminfoTests(unittest.TestCase):
    def test_parses_memory_fields(self):
        text = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         2048000 kB\n"
            "MemAvailable:    4096000 kB\n"
            "Buffers:          512000 kB\n"
            "Cached:          2048000 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1048576 kB\n"
        )
        m = parse_meminfo(text)
        self.assertEqual(m["total_mb"], 16000)
        self.assertEqual(m["avail_mb"], 4000)
        self.assertEqual(m["use_pct"], 75)
        self.assertEqual(m["swap_total_mb"], 2048)
        self.assertEqual(m["swap_used_mb"], 1024)
        self.assertEqual(m["swap_use_pct"], 50)

    def test_handles_missing_mem_available(self):
        text = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         2048000 kB\n"
        )
        m = parse_meminfo(text)
        self.assertEqual(m["use_pct"], 88)  # used = 16000-2000=14000; 14000/16000=87.5→88

    def test_handles_empty_input(self):
        m = parse_meminfo("")
        self.assertEqual(m["total_mb"], 0)
        self.assertEqual(m["use_pct"], 0)


class ParseTopProcessesTests(unittest.TestCase):
    def test_parses_top_processes(self):
        text = (
            "    PID USER     %CPU %MEM COMMAND\n"
            "   1234 mysql     42.3 15.2 /usr/sbin/mysqld\n"
            "   5678 www-data   5.1  2.1 nginx: worker process\n"
        )
        procs = parse_top_processes(text)
        self.assertEqual(len(procs), 2)
        self.assertEqual(procs[0]["pid"], 1234)
        self.assertEqual(procs[0]["user"], "mysql")
        self.assertEqual(procs[0]["cpu_pct"], 42.3)
        self.assertEqual(procs[0]["mem_pct"], 15.2)
        self.assertEqual(procs[0]["command"], "/usr/sbin/mysqld")

    def test_empty_input(self):
        self.assertEqual(parse_top_processes(""), [])

    def test_handles_short_lines(self):
        text = "   1234 mysql     42.3 15.2 mysqld\n"
        procs = parse_top_processes(text)
        self.assertEqual(len(procs), 1)
        self.assertEqual(procs[0]["command"], "mysqld")


class ParseServicesOutputTests(unittest.TestCase):
    def test_parses_active_service(self):
        text = "  nginx.service  loaded active running nginx - web server\n"
        svcs = parse_services_output(text)
        self.assertEqual(len(svcs), 1)
        self.assertEqual(svcs[0]["name"], "nginx")
        self.assertEqual(svcs[0]["state"], "active")
        self.assertEqual(svcs[0]["sub_state"], "running")
        self.assertFalse(svcs[0]["is_abnormal"])

    def test_parses_failed_service(self):
        text = "  myapp.service  loaded failed failed  MyApp crashed\n"
        svcs = parse_services_output(text)
        self.assertEqual(len(svcs), 1)
        self.assertTrue(svcs[0]["is_abnormal"])
        self.assertEqual(svcs[0]["state"], "failed")

    def test_parses_inactive_service(self):
        text = "  thing.service  loaded inactive dead  Unused\n"
        svcs = parse_services_output(text)
        self.assertEqual(len(svcs), 1)
        self.assertTrue(svcs[0]["is_abnormal"])

    def test_exited_is_not_abnormal(self):
        text = "  cron.service  loaded active exited  Cron daemon\n"
        svcs = parse_services_output(text)
        self.assertEqual(len(svcs), 1)
        self.assertFalse(svcs[0]["is_abnormal"])

    def test_handles_blank_lines(self):
        text = "\n  nginx.service  loaded active running nginx\n\n"
        svcs = parse_services_output(text)
        self.assertEqual(len(svcs), 1)

    def test_strips_service_suffix(self):
        text = "  nginx.service  loaded active running nginx\n"
        svcs = parse_services_output(text)
        self.assertEqual(svcs[0]["name"], "nginx")

    def test_ignores_non_service_lines(self):
        text = (
            "  nginx.service  loaded active running nginx\n"
            "  random line that is not a service\n"
            "  myapp.service  loaded failed failed  MyApp\n"
        )
        svcs = parse_services_output(text)
        self.assertEqual(len(svcs), 2)


class PressureLevelTests(unittest.TestCase):
    def test_low_pressure(self):
        self.assertEqual(compute_pressure_level(load_per_core=0.3, mem_pct=50), "low")

    def test_medium_pressure(self):
        self.assertEqual(compute_pressure_level(load_per_core=1.0, mem_pct=80), "medium")

    def test_high_load(self):
        self.assertEqual(compute_pressure_level(load_per_core=2.0, mem_pct=70), "high")

    def test_high_mem(self):
        self.assertEqual(compute_pressure_level(load_per_core=0.5, mem_pct=92), "high")


def _mock_ssh(stdout_text: str, stderr_text: str = "", exit_code: int = 0):
    client = MagicMock()
    fake_stdout = MagicMock()
    fake_stdout.read.return_value = stdout_text.encode("utf-8")
    fake_stdout.channel.recv_exit_status.return_value = exit_code
    fake_stderr = MagicMock()
    fake_stderr.read.return_value = stderr_text.encode("utf-8")
    client.exec_command.return_value = (MagicMock(), fake_stdout, fake_stderr)
    return client


class CheckResourcesHandlerTests(unittest.TestCase):
    def test_returns_structured_result(self):
        stdout = (
            "=== UPTIME ===\n"
            " 14:32:01 up 23 days, 1 user, load average: 0.42, 0.38, 0.41\n"
            "=== NPROC ===\n"
            "4\n"
            "=== FREE ===\n"
            "MemTotal:       16384000 kB\n"
            "MemFree:         2048000 kB\n"
            "MemAvailable:    4096000 kB\n"
            "Buffers:          512000 kB\n"
            "Cached:          2048000 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1048576 kB\n"
            "=== TOP ===\n"
            "    PID USER     %CPU %MEM COMMAND\n"
            "   1234 mysql     42.3 15.2 mysqld\n"
            "   5678 www        5.1  2.1 nginx\n"
        )
        client = _mock_ssh(stdout)
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            out = check_resources_handler({
                "host": "x", "username": "y", "password": "z",
            })
        data = json.loads(out)
        self.assertEqual(data["load_1_5_15"], [0.42, 0.38, 0.41])
        self.assertEqual(data["cpu_cores"], 4)
        self.assertEqual(data["memory"]["total_mb"], 16000)
        self.assertEqual(data["memory"]["used_mb"], 12000)
        self.assertEqual(data["memory"]["use_pct"], 75)
        self.assertEqual(len(data["top_processes"]), 2)
        self.assertEqual(data["top_processes"][0]["user"], "mysql")
        self.assertIn("pressure_level", data)
        self.assertIn("pressure_reasons", data)

    def test_rejects_missing_host(self):
        out = check_resources_handler({"username": "x", "password": "y"})
        self.assertIn("error", json.loads(out))

    def test_rejects_missing_password(self):
        out = check_resources_handler({"host": "x", "username": "y"})
        self.assertIn("error", json.loads(out))

    def test_invalid_port_rejected(self):
        out = check_resources_handler({
            "host": "x", "username": "y", "password": "z", "port": 99999,
        })
        self.assertIn("error", json.loads(out))

    def test_remote_command_failure_returns_error(self):
        client = _mock_ssh("", "command not found", exit_code=127)
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            out = check_resources_handler({
                "host": "x", "username": "y", "password": "z",
            })
        data = json.loads(out)
        self.assertIn("error", data)

    def test_ssh_connection_failure(self):
        client = MagicMock()
        client.connect.side_effect = Exception("Connection refused")
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            out = check_resources_handler({
                "host": "x", "username": "y", "password": "z",
            })
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertIn("SSH error", data["error"])

    def test_connection_always_closed_on_success(self):
        client = _mock_ssh("=== UPTIME ===\n 14:32:01 load average: 0.42, 0.38, 0.41\n"
                            "=== NPROC ===\n4\n=== FREE ===\nMemTotal: 16384000 kB\n"
                            "MemAvailable: 4096000 kB\n"
                            "=== TOP ===\n")
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            check_resources_handler({
                "host": "x", "username": "y", "password": "z",
            })
        client.close.assert_called()

    def test_connection_always_closed_on_exception(self):
        client = MagicMock()
        client.connect.side_effect = Exception("boom")
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            check_resources_handler({
                "host": "x", "username": "y", "password": "z",
            })
        client.close.assert_called()


class ListServicesHandlerTests(unittest.TestCase):
    def test_returns_service_list(self):
        stdout = (
            "  nginx.service  loaded active running nginx - web server\n"
            "  myapp.service  loaded failed failed  MyApp\n"
            "  cron.service   loaded active exited  Cron daemon\n"
        )
        client = _mock_ssh(stdout)
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            out = list_services_handler({
                "host": "x", "username": "y", "password": "z",
            })
        data = json.loads(out)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["abnormal"], 1)
        names = {s["name"] for s in data["services"]}
        self.assertEqual(names, {"nginx", "myapp", "cron"})

    def test_returns_empty_list_when_no_services(self):
        client = _mock_ssh("")
        with patch("hermes.tools.ssh.resources._create_ssh_client", return_value=client):
            out = list_services_handler({
                "host": "x", "username": "y", "password": "z",
            })
        data = json.loads(out)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["abnormal"], 0)
        self.assertEqual(data["services"], [])

    def test_rejects_missing_args(self):
        out = list_services_handler({})
        self.assertIn("error", json.loads(out))


class RegistrationTests(unittest.TestCase):
    def test_check_resources_registered(self):
        from hermes.tools.registry import registry
        self.assertTrue(registry.has("check_resources"))
        self.assertEqual(RESOURCES_SCHEMA["name"], "check_resources")

    def test_list_services_registered(self):
        from hermes.tools.registry import registry
        self.assertTrue(registry.has("list_services"))
        self.assertEqual(SERVICES_SCHEMA["name"], "list_services")


if __name__ == "__main__":
    unittest.main()
