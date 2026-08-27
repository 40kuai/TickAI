"""Tests for hermes.tools.k8s.tools — 6 read-only LLM tools."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPS_DB_PATH", "/tmp/opsticket_test/test.db")
Path("/tmp/opsticket_test").mkdir(parents=True, exist_ok=True)

# Importing has the side-effect of registering the 6 k8s tools
from hermes.tools.k8s import tools as k8s_tools  # noqa: F401, E402
from hermes.tools.registry import registry  # noqa: E402


class RegistrationTests(unittest.TestCase):
    def test_all_six_tools_registered(self):
        expected = {
            "check_k8s_nodes",
            "check_k8s_pods",
            "check_k8s_events",
            "check_k8s_deployments",
            "check_k8s_services",
            "list_k8s_contexts",
        }
        actual = {s["name"] for s in registry.list_schemas()}
        for name in expected:
            self.assertIn(name, actual, f"{name} should be registered")


class ListContextsTests(unittest.TestCase):
    def test_returns_parsed_contexts(self):
        # `kubectl config get-contexts` returns tabular text, not JSON.
        # We parse it via run_kubectl (text), not run_kubectl_json.
        fake_stdout = (
            "CURRENT   NAME           CLUSTER        AUTHINFO      NAMESPACE\n"
            "*         prod-cluster   prod-cluster   prod-admin\n"
            "          dev-cluster    dev-cluster    dev-user       dev-ns\n"
        )
        with patch("hermes.tools.k8s.tools.run_kubectl",
                    return_value=(fake_stdout, "", 0)):
            out = registry.dispatch("list_k8s_contexts", {})
        data = json.loads(out)
        self.assertIn("contexts", data)
        self.assertEqual(len(data["contexts"]), 2)
        self.assertEqual(data["contexts"][0]["name"], "prod-cluster")
        self.assertTrue(data["contexts"][0]["is_current"])
        self.assertFalse(data["contexts"][1]["is_current"])

    def test_handles_error(self):
        with patch("hermes.tools.k8s.tools.run_kubectl",
                    return_value=("", "kubectl not found", 1)):
            out = registry.dispatch("list_k8s_contexts", {})
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertIn("kubectl not found", data["error"])


class GetResourceTests(unittest.TestCase):
    """Common pattern for check_k8s_nodes / pods / events / deployments / services."""

    def _check_dispatch(self, tool_name: str, kubectl_argv: list, expected_kw: str):
        with patch("hermes.tools.k8s.tools.run_kubectl_json",
                    return_value={"items": []}) as m:
            out = registry.dispatch(tool_name, {"context": "prod"})
        data = json.loads(out)
        self.assertIn("items", data)
        # verify the call to kubectl used the right context
        called_argv = m.call_args[0][0]
        self.assertIn("--context", called_argv)
        self.assertIn("prod", called_argv)
        # -A flag expected for cluster-wide resources
        if expected_kw == "cluster-wide":
            self.assertIn("-A", called_argv)
        # -o json always
        self.assertIn("-o", called_argv)
        idx = called_argv.index("-o")
        self.assertEqual(called_argv[idx + 1], "json")

    def test_check_k8s_nodes_uses_context(self):
        # nodes don't need -A (cluster-scoped)
        with patch("hermes.tools.k8s.tools.run_kubectl_json",
                    return_value={"items": []}) as m:
            out = registry.dispatch("check_k8s_nodes", {"context": "prod"})
        called_argv = m.call_args[0][0]
        self.assertIn("--context", called_argv)
        self.assertNotIn("-A", called_argv)
        self.assertEqual(called_argv[1], "nodes")  # argv[0]=get, argv[1]=nodes

    def test_check_k8s_pods_uses_all_namespaces(self):
        self._check_dispatch("check_k8s_pods",
                              ["get", "pods", "-A", "--context", "prod", "-o", "json"],
                              "cluster-wide")

    def test_check_k8s_events_uses_all_namespaces(self):
        self._check_dispatch("check_k8s_events",
                              ["get", "events", "-A", "--context", "prod", "-o", "json"],
                              "cluster-wide")

    def test_check_k8s_deployments_uses_all_namespaces(self):
        self._check_dispatch("check_k8s_deployments",
                              ["get", "deployments", "-A", "--context", "prod", "-o", "json"],
                              "cluster-wide")

    def test_check_k8s_services_uses_all_namespaces(self):
        self._check_dispatch("check_k8s_services",
                              ["get", "services", "-A", "--context", "prod", "-o", "json"],
                              "cluster-wide")

    def test_supports_namespace_filter_for_pods(self):
        with patch("hermes.tools.k8s.tools.run_kubectl_json",
                    return_value={"items": []}) as m:
            registry.dispatch("check_k8s_pods",
                               {"context": "prod", "namespace": "kube-system"})
        called_argv = m.call_args[0][0]
        self.assertIn("-n", called_argv)
        self.assertIn("kube-system", called_argv)
        self.assertNotIn("-A", called_argv)

    def test_supports_field_selector(self):
        with patch("hermes.tools.k8s.tools.run_kubectl_json",
                    return_value={"items": []}) as m:
            registry.dispatch("check_k8s_pods",
                               {"context": "prod",
                                "field_selector": "status.phase=Running"})
        called_argv = m.call_args[0][0]
        self.assertIn("--field-selector", called_argv)
        self.assertIn("status.phase=Running", called_argv)

    def test_returns_error_on_kubectl_failure(self):
        with patch("hermes.tools.k8s.tools.run_kubectl_json",
                    return_value={"error": "connection refused"}):
            out = registry.dispatch("check_k8s_nodes", {"context": "prod"})
        data = json.loads(out)
        self.assertIn("error", data)


class SchemaTests(unittest.TestCase):
    """Verify schema descriptions are useful for the LLM (not just stubs)."""

    def test_check_k8s_nodes_description_mentions_readonly(self):
        schema = next(s for s in registry.list_schemas() if s["name"] == "check_k8s_nodes")
        self.assertIn("read-only", schema["description"].lower())

    def test_all_schemas_require_context_or_have_default(self):
        # All check_* tools take a context (optional in our design)
        for s in registry.list_schemas():
            if s["name"].startswith("check_k8s_"):
                props = s["parameters"]["properties"]
                self.assertIn("context", props, f"{s['name']} should accept context")


if __name__ == "__main__":
    unittest.main()
