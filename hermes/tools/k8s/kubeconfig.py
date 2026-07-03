"""Read-only kubeconfig YAML parser.

For the K8s tools, we use kubectl for the actual queries (it handles auth,
context switching, etc.). This module exists for:

  1. Validation — check that a context name exists before passing to kubectl
  2. Pre-flight checks — list available contexts, identify current context
  3. Diagnostics — surface clear errors to the user when context is wrong

We use PyYAML for parsing. If it's not installed, we fail fast with a clear
message.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyYAML is required for kubeconfig parsing. "
        "Install with: pip install pyyaml"
    ) from exc


DEFAULT_KUBECONFIG = Path("~/.kube/config").expanduser()


class KubeconfigError(ValueError):
    """Raised when the kubeconfig cannot be parsed or is invalid."""


def _load_yaml(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        raise KubeconfigError(f"kubeconfig not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise KubeconfigError(f"cannot read kubeconfig: {exc}") from exc
    if not text.strip():
        raise KubeconfigError(f"kubeconfig is empty: {p}")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise KubeconfigError(f"invalid YAML in kubeconfig: {exc}") from exc
    if not isinstance(data, dict):
        raise KubeconfigError(f"kubeconfig root must be a mapping, got {type(data).__name__}")
    return data


def list_contexts(path: str | Path = DEFAULT_KUBECONFIG) -> List[Dict[str, str]]:
    """Return list of contexts defined in the kubeconfig.

    Each item: {"name": "...", "cluster": "...", "user": "..."}
    """
    data = _load_yaml(str(path))
    contexts = data.get("contexts")
    if not contexts:
        raise KubeconfigError(f"no contexts defined in {path}")
    out: List[Dict[str, str]] = []
    for ctx in contexts:
        if not isinstance(ctx, dict):
            continue
        name = ctx.get("name")
        ctx_body = ctx.get("context") or {}
        if not name:
            continue
        out.append({
            "name": str(name),
            "cluster": str(ctx_body.get("cluster", "")),
            "user": str(ctx_body.get("user", "")),
        })
    if not out:
        raise KubeconfigError(f"no usable contexts in {path}")
    return out


def get_current_context(path: str | Path = DEFAULT_KUBECONFIG) -> str:
    """Return the name of the current-context. Empty string if not set."""
    data = _load_yaml(str(path))
    return str(data.get("current-context") or "")


def context_exists(path: str | Path, name: str) -> bool:
    """True if a context with this name is defined in the kubeconfig."""
    try:
        names = {c["name"] for c in list_contexts(path)}
    except KubeconfigError:
        return False
    return name in names


# ============================================================
# Environment variable management
# ============================================================

def current_kubeconfig_path() -> Path:
    """Return the active kubeconfig path (KUBECONFIG env var or ~/.kube/config)."""
    env = os.environ.get("KUBECONFIG")
    if env:
        return Path(env)
    return DEFAULT_KUBECONFIG


@contextmanager
def with_kubeconfig(path: str | Path):
    """Context manager: temporarily set KUBECONFIG env var.

    Inside the block, kubectl subprocess calls (and any other tool that
    respects KUBECONFIG) will use this path. Outside the block, the
    previous KUBECONFIG (or absence) is restored.

    Empty/None path means "use default" — KUBECONFIG is removed for the
    duration of the block, then restored to its previous value.
    """
    previous = os.environ.get("KUBECONFIG")
    had_previous = "KUBECONFIG" in os.environ
    try:
        if path:
            os.environ["KUBECONFIG"] = str(path)
        else:
            os.environ.pop("KUBECONFIG", None)
        yield
    finally:
        if had_previous:
            os.environ["KUBECONFIG"] = previous
        else:
            os.environ.pop("KUBECONFIG", None)


def list_contexts_safe(path: str | Path = "") -> list[dict]:
    """Like list_contexts, but never raises — returns [] on any error.

    Use this for UI code where you want to render "no contexts" instead
    of an error. Pass empty string to indicate "no kubeconfig selected".
    """
    if not path:
        return []
    try:
        return list_contexts(path)
    except KubeconfigError:
        return []
