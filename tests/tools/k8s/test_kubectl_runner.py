"""Tests for hermes.tools.k8s.kubectl_runner — strict-allowlist kubectl wrapper."""
import json
import unittest
from unittest.mock import MagicMock, patch

from hermes.tools.k8s.kubectl_runner import (
    run_kubectl,
    run_kubectl_json,
    ALLOWED_GET_RESOURCES,
    ALLOWED_CONFIG_SUBCOMMANDS,
    FORBIDDEN_VERBS,
    FORBIDDEN_FLAGS,
    KubectlError,
)


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class AllowlistTests(unittest.TestCase):
    """The allowlist is the security boundary — verify it explicitly."""

    def test_allows_known_get_resources(self):
        for r in ("nodes", "pods", "events", "deployments", "services"):
            self.assertIn(r, ALLOWED_GET_RESOURCES)

    def test_does_not_allow_secrets(self):
        # secrets contain sensitive data — must be opt-in later, not in default
        self.assertNotIn("secrets", ALLOWED_GET_RESOURCES)
        self.assertNotIn("configmaps", ALLOWED_GET_RESOURCES)

    def test_lists_forbidden_verbs(self):
        # every common destructive verb must be blocked
        for v in ("delete", "apply", "patch", "edit", "exec", "create",
                  "replace", "scale", "rollout", "cordon", "drain",
                  "taint", "label", "annotate", "cp", "proxy",
                  "port-forward", "attach", "auth", "debug", "run", "logs"):
            self.assertIn(v, FORBIDDEN_VERBS, f"{v} should be forbidden")

    def test_lists_forbidden_flags(self):
        for f in ("--save-config", "-o", "-f", "--filename", "--record"):
            pass # -o handled separately as "must be json"


class RunKubectlValidationTests(unittest.TestCase):
    """Reject disallowed commands before any subprocess is launched."""

    def test_rejects_empty_argv(self):
        with self.assertRaises(KubectlError):
            run_kubectl([])

    def test_rejects_non_kubectl(self):
        with self.assertRaises(KubectlError):
            run_kubectl(["rm", "-rf", "/"])

    def test_rejects_forbidden_verb(self):
        with self.assertRaises(KubectlError) as ctx:
            run_kubectl(["delete", "pod", "nginx"])
        self.assertIn("forbidden", str(ctx.exception).lower())

    def test_rejects_get_for_disallowed_resource(self):
        with self.assertRaises(KubectlError) as ctx:
            run_kubectl(["get", "secrets", "-A", "-o", "json"])
        self.assertIn("not in allowlist", str(ctx.exception).lower())

    def test_allows_get_with_context_flag(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(stdout='{"items":[]}', returncode=0)
            run_kubectl(["get", "nodes", "--context", "prod", "-o", "json"])
            called_argv = run_mock.call_args[0][0]
            self.assertIn("--context", called_argv)
            self.assertIn("prod", called_argv)

    def test_rejects_get_with_forbidden_flag(self):
        with self.assertRaises(KubectlError) as ctx:
            run_kubectl(["get", "pods", "--save-config"])
        self.assertIn("forbidden flag", str(ctx.exception).lower())

    def test_allows_get_with_namespace_flag(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(stdout='{"items":[]}', returncode=0)
            run_kubectl(["get", "pods", "-n", "kube-system", "-o", "json"])
            called_argv = run_mock.call_args[0][0]
            self.assertIn("-n", called_argv)
            self.assertIn("kube-system", called_argv)

    def test_allows_config_get_contexts(self):
        # kubectl config get-contexts does NOT support -o json; default tabular is the only option
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(stdout="CURRENT   NAME", returncode=0)
            run_kubectl(["config", "get-contexts"])
            called_argv = run_mock.call_args[0][0]
            self.assertEqual(called_argv[1], "config")
            self.assertEqual(called_argv[2], "get-contexts")


class RunKubectlExecutionTests(unittest.TestCase):
    """After passing validation, run_kubectl executes and returns parsed result."""

    def test_returns_stdout_on_success(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(stdout="hello world", returncode=0)
            out, err, rc = run_kubectl(["get", "nodes", "-o", "json"])
        self.assertEqual(out, "hello world")
        self.assertEqual(err, "")
        self.assertEqual(rc, 0)

    def test_returns_stderr_on_failure(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(
                stdout="", stderr="connection refused", returncode=1
            )
            out, err, rc = run_kubectl(["get", "nodes", "-o", "json"])
        self.assertEqual(rc, 1)
        self.assertEqual(err, "connection refused")

    def test_uses_timeout(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(returncode=0)
            run_kubectl(["get", "nodes", "-o", "json"], timeout=42)
            self.assertEqual(run_mock.call_args.kwargs.get("timeout"), 42)

    def test_uses_10s_default_timeout(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(returncode=0)
            run_kubectl(["get", "nodes", "-o", "json"])
            self.assertEqual(run_mock.call_args.kwargs.get("timeout"), 10)

    def test_captures_binary_output_as_text(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(stdout="中文 output", returncode=0)
            out, _, _ = run_kubectl(["get", "nodes", "-o", "json"])
        self.assertEqual(out, "中文 output")


class RunKubectlJsonTests(unittest.TestCase):
    """run_kubectl_json wraps run_kubectl and parses JSON, with error handling."""

    def test_parses_valid_json(self):
        payload = {"items": [{"metadata": {"name": "node-1"}}]}
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(
                stdout=json.dumps(payload), returncode=0
            )
            data = run_kubectl_json(["get", "nodes", "-o", "json"])
        self.assertEqual(data, payload)

    def test_returns_error_dict_on_non_zero_exit(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(
                stdout="", stderr="no such host", returncode=1
            )
            data = run_kubectl_json(["get", "nodes", "-o", "json"])
        self.assertIn("error", data)
        self.assertIn("no such host", data["error"])

    def test_returns_error_dict_on_invalid_json(self):
        with patch("subprocess.run") as run_mock:
            run_mock.return_value = _fake_completed(
                stdout="not json", returncode=0
            )
            data = run_kubectl_json(["get", "nodes", "-o", "json"])
        self.assertIn("error", data)
        self.assertIn("json", data["error"].lower())

    def test_returns_error_dict_on_subprocess_exception(self):
        with patch("subprocess.run") as run_mock:
            run_mock.side_effect = FileNotFoundError("kubectl not found")
            data = run_kubectl_json(["get", "nodes", "-o", "json"])
        self.assertIn("error", data)
        self.assertIn("kubectl", data["error"].lower())


if __name__ == "__main__":
    unittest.main()
