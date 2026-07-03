"""Tests for hermes.tools.k8s.kubeconfig — env-var helpers."""
import os
import tempfile
import unittest
from pathlib import Path

from hermes.tools.k8s.kubeconfig import (
    with_kubeconfig,
    KubeconfigError,
    list_contexts_safe,
    current_kubeconfig_path,
)


SAMPLE_YAML = """\
apiVersion: v1
kind: Config
current-context: prod
contexts:
- name: prod
  context: {cluster: prod-c, user: admin}
- name: dev
  context: {cluster: dev-c, user: dev}
"""


class WithKubeconfigTests(unittest.TestCase):
    def setUp(self):
        # Ensure no inherited KUBECONFIG
        self._old = os.environ.pop("KUBECONFIG", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KUBECONFIG", None)
        else:
            os.environ["KUBECONFIG"] = self._old

    def test_sets_env_var(self):
        with with_kubeconfig("/some/path/config"):
            self.assertEqual(os.environ["KUBECONFIG"], "/some/path/config")

    def test_restores_original_when_empty(self):
        # No original KUBECONFIG set
        self.assertNotIn("KUBECONFIG", os.environ)
        with with_kubeconfig("/some/path/config"):
            self.assertEqual(os.environ["KUBECONFIG"], "/some/path/config")
        # After exit, KUBECONFIG should be removed
        self.assertNotIn("KUBECONFIG", os.environ)

    def test_restores_existing_env_var(self):
        os.environ["KUBECONFIG"] = "/original/path"
        with with_kubeconfig("/override/path"):
            self.assertEqual(os.environ["KUBECONFIG"], "/override/path")
        self.assertEqual(os.environ["KUBECONFIG"], "/original/path")

    def test_empty_path_removes_env_var(self):
        os.environ["KUBECONFIG"] = "/some/path"
        with with_kubeconfig(""):
            # Empty path = use default = remove KUBECONFIG
            self.assertNotIn("KUBECONFIG", os.environ)
        # After exit, original is restored
        self.assertEqual(os.environ["KUBECONFIG"], "/some/path")

    def test_restores_on_exception(self):
        os.environ["KUBECONFIG"] = "/original"
        try:
            with with_kubeconfig("/override"):
                raise RuntimeError("test exception")
        except RuntimeError:
            pass
        self.assertEqual(os.environ["KUBECONFIG"], "/original")


class ListContextsSafeTests(unittest.TestCase):
    def test_returns_contexts_for_valid_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(SAMPLE_YAML)
            path = f.name
        try:
            contexts = list_contexts_safe(path)
            self.assertEqual(len(contexts), 2)
            names = {c["name"] for c in contexts}
            self.assertEqual(names, {"prod", "dev"})
        finally:
            Path(path).unlink(missing_ok=True)

    def test_returns_empty_for_missing_path(self):
        contexts = list_contexts_safe("/nonexistent/path/config")
        self.assertEqual(contexts, [])

    def test_returns_empty_for_empty_path(self):
        # Empty path = no kubeconfig selected
        contexts = list_contexts_safe("")
        # Should return empty (caller is responsible for default behavior)
        self.assertEqual(contexts, [])

    def test_returns_empty_for_invalid_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("not: valid: yaml: ::")
            path = f.name
        try:
            contexts = list_contexts_safe(path)
            self.assertEqual(contexts, [])
        finally:
            Path(path).unlink(missing_ok=True)


class CurrentKubeconfigPathTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop("KUBECONFIG", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("KUBECONFIG", None)
        else:
            os.environ["KUBECONFIG"] = self._old

    def test_returns_default_when_not_set(self):
        self.assertNotIn("KUBECONFIG", os.environ)
        path = current_kubeconfig_path()
        self.assertTrue(str(path).endswith(".kube/config"))

    def test_returns_env_var_when_set(self):
        os.environ["KUBECONFIG"] = "/custom/path"
        self.assertEqual(str(current_kubeconfig_path()), "/custom/path")


if __name__ == "__main__":
    unittest.main()
