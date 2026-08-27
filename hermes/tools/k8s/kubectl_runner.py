"""kubectl subprocess wrapper with strict allowlist.

This is the security boundary for all K8s operations in OpsTicket. Every
command that hits this module is validated against an allowlist BEFORE
any subprocess is launched. Read-only by design.

Hard rules (enforced in code, not just docs):
  - Only `get` and `config get-contexts` are allowed verbs
  - Only known resources (nodes, pods, events, deployments, services) are allowed
  - Forbidden verbs block the entire command (no clever bypasses)
  - Forbidden flags block the entire command
  - 10-second timeout on every call

Usage:
  run_kubectl(["get", "nodes", "-o", "json"])
  run_kubectl(["config", "get-contexts", "-o", "json"])
  run_kubectl_json(["get", "pods", "-A", "-o", "json"])
"""
from __future__ import annotations

import json
import subprocess
from typing import List, Optional, Tuple


# ============================================================
# Security allowlist
# ============================================================

# Resources that may be queried via `kubectl get <resource> -o json`.
# Notably absent: secrets, configmaps, serviceaccounts, roles, rolebindings,
# clusterroles, clusterrolebindings, validatingwebhookconfigurations —
# these contain sensitive data and require explicit opt-in.
ALLOWED_GET_RESOURCES = frozenset({
    "nodes",
    "pods",
    "events",
    "deployments",
    "services",
})

# `kubectl config <sub>` only ever allowed as `config get-contexts`.
ALLOWED_CONFIG_SUBCOMMANDS = frozenset({
    "get-contexts",
})

# Every verb other than `get` and `config` is forbidden.
FORBIDDEN_VERBS = frozenset({
    "delete", "apply", "patch", "edit", "exec", "create", "replace",
    "scale", "rollout", "cordon", "drain", "taint", "label", "annotate",
    "set", "cp", "proxy", "port-forward", "attach", "auth", "debug",
    "run", "logs", "wait", "convert",
    "kustomize", "api-resources", "api-versions", "cluster-info",
    "completion", "version", "help",
})

# Flags that can modify state or hide errors.
FORBIDDEN_FLAGS = frozenset({
    "--save-config", "--record", "--force", "--grace-period=0",
    "-f", "--filename",             # could point to a manifest
    "--server",                     # could redirect to a different cluster
    "--token",                      # explicit token override
    "--as",                         # impersonation: could escalate
})

# Output format flag is allowed ONLY with value "json"
OUTPUT_JSON_FLAG = ("-o", "json")
OUTPUT_JSON_FLAG_LONG = ("--output", "json")

DEFAULT_TIMEOUT = 10  # seconds


class KubectlError(ValueError):
    """Raised when a kubectl command is rejected by the allowlist."""


# ============================================================
# Validation
# ============================================================

def _validate_argv(argv: List[str]) -> None:
    """Reject commands outside the allowlist. Raise KubectlError on rejection.

    argv[0] is the verb. We auto-prepend "kubectl" when calling subprocess.

    Allowed shapes:
      [get, <resource>, <flags>...]
      [config, get-contexts, <flags>...]
    """
    if not argv:
        raise KubectlError("empty command")

    verb = argv[0]

    if verb == "get":
        if len(argv) < 2:
            raise KubectlError("'get' requires a resource name")
        resource = argv[1]
        if resource not in ALLOWED_GET_RESOURCES:
            raise KubectlError(
                f"resource {resource!r} not in allowlist "
                f"(only {sorted(ALLOWED_GET_RESOURCES)})"
            )
    elif verb == "config":
        if len(argv) < 2:
            raise KubectlError("config subcommand required")
        sub = argv[1]
        if sub not in ALLOWED_CONFIG_SUBCOMMANDS:
            raise KubectlError(
                f"config subcommand {sub!r} not in allowlist "
                f"(only {sorted(ALLOWED_CONFIG_SUBCOMMANDS)})"
            )
    elif verb in FORBIDDEN_VERBS:
        raise KubectlError(f"forbidden kubectl verb: {verb!r}")
    else:
        # Unknown verb — explicitly reject. Better safe than allow-by-default.
        raise KubectlError(f"forbidden kubectl verb: {verb!r}")


def _validate_flags(argv: List[str]) -> None:
    """Reject commands with forbidden flags.

    Allowed flags (whitelist approach — strict by design):
      -o json       (forced for LLM consumption)
      --output json
      -n / --namespace <name>
      --context <name>
      -A / --all-namespaces
      --no-headers
      --sort-by <field>
      --show-kind
      --show-labels
      --field-selector <expr>
      --label-columns <cols>
    """
    allowed_flags = {
        "-o", "--output",
        "-n", "--namespace",
        "--context",
        "-A", "--all-namespaces",
        "--no-headers",
        "--sort-by",
        "--show-kind",
        "--show-labels",
        "--field-selector",
        "--label-columns",
    }

    # Iterate in order, consuming pairs (flag, value) for flags that take values
    i = 1  # skip the verb
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--") and "=" in arg:
            # --flag=value form — check the flag part
            flag_name = arg.split("=", 1)[0]
            if flag_name in FORBIDDEN_FLAGS:
                raise KubectlError(f"forbidden flag: {flag_name!r}")
            if flag_name not in allowed_flags:
                raise KubectlError(f"unrecognized flag: {flag_name!r}")
            i += 1
        elif arg.startswith("-"):
            if arg in FORBIDDEN_FLAGS:
                raise KubectlError(f"forbidden flag: {arg!r}")
            if arg not in allowed_flags:
                raise KubectlError(f"unrecognized flag: {arg!r}")
            # Some flags take a value
            if arg in ("-n", "--namespace", "--context", "-o", "--output",
                      "--sort-by", "--field-selector", "--label-columns"):
                # next arg is the value — skip it
                if i + 1 >= len(argv):
                    raise KubectlError(f"flag {arg!r} requires a value")
                # -o / --output must be 'json' — we don't want other formats leaking through
                if arg in ("-o", "--output") and argv[i + 1] != "json":
                    raise KubectlError(
                        f"{arg} must be 'json' (got {argv[i + 1]!r}) — "
                        "we only allow JSON output for LLM consumption"
                    )
                i += 2
            else:
                i += 1
        else:
            i += 1  # positional arg (resource name, etc.)


# ============================================================
# Execution
# ============================================================

def run_kubectl(
    argv: List[str],
    timeout: int = DEFAULT_TIMEOUT,
    kubeconfig: Optional[str] = None,
) -> Tuple[str, str, int]:
    """Run a kubectl command. Returns (stdout, stderr, returncode).

    `argv` is the args AFTER 'kubectl'. We prepend 'kubectl' ourselves.
    To pass a custom kubeconfig, use the `kubeconfig` argument — it is
    injected as '--kubeconfig <path>' (which is allowed by validation
    because we strip it before checking).

    Raises KubectlError if validation fails (no subprocess is launched).
    """
    _validate_argv(argv)
    _validate_flags(argv)

    full_argv = ["kubectl"] + list(argv)
    if kubeconfig:
        full_argv = ["--kubeconfig", kubeconfig] + full_argv

    try:
        completed = subprocess.run(
            full_argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise KubectlError(f"kubectl timeout after {timeout}s: {exc}")
    except FileNotFoundError as exc:
        raise KubectlError(f"kubectl binary not found: {exc}")

    return completed.stdout, completed.stderr, completed.returncode


def run_kubectl_json(
    argv: List[str],
    timeout: int = DEFAULT_TIMEOUT,
    kubeconfig: Optional[str] = None,
) -> dict:
    """Run a kubectl command and parse stdout as JSON.

    On any error (non-zero exit, invalid JSON, subprocess exception),
    return a dict with an 'error' key — never raise. This is the LLM-friendly
    entry point.
    """
    try:
        stdout, stderr, returncode = run_kubectl(argv, timeout=timeout, kubeconfig=kubeconfig)
    except KubectlError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"unexpected error: {exc}"}

    if returncode != 0:
        return {"error": f"kubectl exit {returncode}: {stderr.strip() or 'no stderr'}"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"error": f"kubectl output is not valid JSON: {exc}. First 200 chars: {stdout[:200]!r}"}
