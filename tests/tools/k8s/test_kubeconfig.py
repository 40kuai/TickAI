"""Tests for hermes.tools.k8s.kubeconfig — parse ~/.kube/config."""
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from hermes.tools.k8s.kubeconfig import (
    KubeconfigError,
    list_contexts,
    get_current_context,
    context_exists,
    DEFAULT_KUBECONFIG,
)


SAMPLE_YAML = """\
apiVersion: v1
kind: Config
current-context: prod-cluster
clusters:
- name: prod-cluster
  cluster:
    server: https://prod.example.com
- name: dev-cluster
  cluster:
    server: https://dev.example.com
contexts:
- name: prod-cluster
  context:
    cluster: prod-cluster
    user: prod-admin
- name: dev-cluster
  context:
    cluster: dev-cluster
    user: dev-user
users:
- name: prod-admin
  user:
    token: secret-prod
- name: dev-user
  user:
    token: secret-dev
"""


class KubeconfigFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        self.tmp.write(SAMPLE_YAML)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        Path(self.path).unlink(missing_ok=True)

    def test_list_contexts(self):
        contexts = list_contexts(self.path)
        names = [c["name"] for c in contexts]
        self.assertEqual(names, ["prod-cluster", "dev-cluster"])

    def test_list_contexts_includes_cluster(self):
        contexts = list_contexts(self.path)
        prod = next(c for c in contexts if c["name"] == "prod-cluster")
        self.assertEqual(prod["cluster"], "prod-cluster")

    def test_get_current_context(self):
        self.assertEqual(get_current_context(self.path), "prod-cluster")

    def test_context_exists(self):
        self.assertTrue(context_exists(self.path, "prod-cluster"))
        self.assertTrue(context_exists(self.path, "dev-cluster"))
        self.assertFalse(context_exists(self.path, "nonexistent"))

    def test_default_kubeconfig_constant(self):
        self.assertTrue(str(DEFAULT_KUBECONFIG).endswith(".kube/config"))


class KubeconfigErrorTests(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(KubeconfigError):
            list_contexts("/nonexistent/kubeconfig")

    def test_invalid_yaml_raises(self):
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("this is: not valid yaml: : :")
            path = f.name
        try:
            with self.assertRaises(KubeconfigError):
                list_contexts(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_empty_file_raises(self):
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            path = f.name
        try:
            with self.assertRaises(KubeconfigError):
                list_contexts(path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_no_contexts_raises(self):
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("apiVersion: v1\nkind: Config\n")
            path = f.name
        try:
            with self.assertRaises(KubeconfigError):
                list_contexts(path)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
