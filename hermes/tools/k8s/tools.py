"""6 LLM-callable K8s tools, all strictly read-only.

These are the only K8s operations the LLM can perform. They map 1:1 to
the hardcoded allowlist in kubectl_runner (ALLOWED_GET_RESOURCES).

  - check_k8s_nodes         : cluster nodes + status
  - check_k8s_pods          : all pods (or filtered by namespace)
  - check_k8s_events        : cluster events (sorted by lastTimestamp)
  - check_k8s_deployments   : all deployments
  - check_k8s_services      : all services
  - list_k8s_contexts       : available kubeconfig contexts

All take an optional `context` (kubeconfig context name) and a `namespace`
where applicable. They use run_kubectl_json which is the safe subprocess
wrapper — no command other than `kubectl get ...` is ever invoked.
"""
from __future__ import annotations

from typing import Any, Optional

from hermes.tools.registry import registry, tool_error, tool_result

from .kubectl_runner import run_kubectl, run_kubectl_json


def _add_context(argv: list, context: Optional[str]) -> list:
    if context:
        return argv + ["--context", context]
    return argv


def _add_namespace_filter(argv: list, namespace: Optional[str], all_namespaces: bool) -> list:
    if namespace:
        return argv + ["-n", namespace]
    if all_namespaces:
        return argv + ["-A"]
    return argv


def _add_field_selector(argv: list, field_selector: Optional[str]) -> list:
    if field_selector:
        return argv + ["--field-selector", field_selector]
    return argv


def _common_handler(
    args: dict,
    resource: str,
    all_namespaces: bool = True,
    allow_namespace_filter: bool = True,
) -> str:
    context = args.get("context")
    namespace = args.get("namespace") if allow_namespace_filter else None
    field_selector = args.get("field_selector") if allow_namespace_filter else None

    argv = ["get", resource, "-o", "json"]
    argv = _add_context(argv, context)
    argv = _add_namespace_filter(argv, namespace, all_namespaces)
    argv = _add_field_selector(argv, field_selector)

    result = run_kubectl_json(argv)
    if "error" in result:
        return tool_error(result["error"])

    # Truncate very large outputs to keep LLM context manageable
    items = result.get("items", [])
    if len(items) > 200:
        return tool_result(
            items=items[:200],
            total=len(items),
            truncated=True,
            note=f"showing first 200 of {len(items)} {resource} — narrow your filter to see more",
        )

    return tool_result(items=items, total=len(items), truncated=False)


# ============================================================
# Tool 1: list_k8s_contexts
# ============================================================

def list_k8s_contexts_handler(args: dict, **kwargs) -> str:
    # `kubectl config get-contexts` does NOT support -o json. We must use
    # run_kubectl (text) and parse the tabular output.
    argv = ["config", "get-contexts"]
    argv = _add_context(argv, args.get("context"))  # unusual but valid

    try:
        stdout, stderr, returncode = run_kubectl(argv)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"failed to run kubectl: {exc}")

    if returncode != 0:
        return tool_error(f"kubectl exit {returncode}: {stderr.strip() or 'no stderr'}")

    contexts = _parse_contexts_tabular(stdout)
    return tool_result(contexts=contexts, count=len(contexts))


def _parse_contexts_tabular(raw: str) -> list[dict]:
    """Parse `kubectl config get-contexts` tabular output.

    Lines look like:
        CURRENT   NAME           CLUSTER        AUTHINFO      NAMESPACE
        *         prod-cluster   prod-cluster   prod-admin
                  dev-cluster    dev-cluster    dev-user       dev-ns
    """
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        parts = line.split()
        # Skip header row: "CURRENT NAME CLUSTER AUTHINFO NAMESPACE"
        if parts and parts[0] == "CURRENT":
            continue
        # First column may be '*' for current
        is_current = line.lstrip().startswith("*")
        if is_current:
            parts = parts[1:]
        if len(parts) < 3:
            continue
        out.append({
            "name": parts[0],
            "cluster": parts[1] if len(parts) > 1 else "",
            "user": parts[2] if len(parts) > 2 else "",
            "namespace": parts[3] if len(parts) > 3 else "",
            "is_current": is_current,
        })
    return out


# ============================================================
# Tool 2: check_k8s_nodes (cluster-scoped, no -A)
# ============================================================

def check_k8s_nodes_handler(args: dict, **kwargs) -> str:
    return _common_handler(args, "nodes", all_namespaces=False, allow_namespace_filter=False)


# ============================================================
# Tools 3-6: cluster-wide with namespace/field-selector support
# ============================================================

def check_k8s_pods_handler(args: dict, **kwargs) -> str:
    return _common_handler(args, "pods")


def check_k8s_events_handler(args: dict, **kwargs) -> str:
    # Events are best viewed sorted by lastTimestamp (most recent first)
    context = args.get("context")
    argv = ["get", "events", "-A", "--sort-by", ".lastTimestamp", "-o", "json"]
    argv = _add_context(argv, context)
    result = run_kubectl_json(argv)
    if "error" in result:
        return tool_error(result["error"])
    items = result.get("items", [])
    # Keep only Warning type events by default — those are the interesting ones
    if not args.get("include_normal", False):
        items = [e for e in items if e.get("type") == "Warning"]
    if len(items) > 200:
        items = items[:200]
        truncated = True
    else:
        truncated = False
    return tool_result(items=items, total=len(items), truncated=truncated)


def check_k8s_deployments_handler(args: dict, **kwargs) -> str:
    return _common_handler(args, "deployments")


def check_k8s_services_handler(args: dict, **kwargs) -> str:
    return _common_handler(args, "services")


# ============================================================
# Schemas
# ============================================================

CONTEXT_PARAM = {
    "context": {
        "type": "string",
        "description": (
            "Kubeconfig context name (e.g., 'prod-cluster'). "
            "If omitted, uses the current-context from ~/.kube/config. "
            "Use list_k8s_contexts to discover available contexts."
        ),
    }
}

COMMON_PARAMS = {
    "type": "object",
    "properties": {
        **CONTEXT_PARAM,
        "namespace": {
            "type": "string",
            "description": "Filter to a single namespace. Mutually exclusive with all-namespaces.",
        },
        "field_selector": {
            "type": "string",
            "description": "kubectl field-selector expression (e.g., 'status.phase=Running').",
        },
    },
}


NODES_SCHEMA = {
    "name": "check_k8s_nodes",
    "description": (
        "Read-only inspection of all Kubernetes nodes. Returns node status, "
        "capacity, allocatable resources, and any conditions (Ready, MemoryPressure, "
        "DiskPressure, etc.). NEVER modifies the cluster.\n\n"
        "Use this to diagnose cluster-wide pressure — for example, when services "
        "report 'no nodes available' or pods are stuck in Pending. Look at the "
        "'conditions' field for pressure signals."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "context": CONTEXT_PARAM["context"],
        },
    },
}

PODS_SCHEMA = {
    "name": "check_k8s_pods",
    "description": (
        "Read-only listing of Kubernetes pods. Returns pod phase, container "
        "status, restart counts, and resource requests/limits. NEVER modifies the cluster.\n\n"
        "Use this to find unhealthy pods, find pods without resource limits, or "
        "audit workloads. Supports filtering by namespace or field-selector."
    ),
    "parameters": COMMON_PARAMS,
}

EVENTS_SCHEMA = {
    "name": "check_k8s_events",
    "description": (
        "Read-only listing of Kubernetes events sorted by most recent. "
        "Returns Warning events by default (FailedScheduling, OOMKilling, Backoff, "
        "FailedMount, etc.). Set include_normal=true to see all event types. "
        "NEVER modifies the cluster.\n\n"
        "Use this to root-cause why pods are failing or stuck. OOMKilling events "
        "specifically indicate a container was killed for using too much memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **CONTEXT_PARAM,
            "include_normal": {
                "type": "boolean",
                "default": False,
                "description": "Include Normal events. Default is Warning only.",
            },
        },
    },
}

DEPLOYMENTS_SCHEMA = {
    "name": "check_k8s_deployments",
    "description": (
        "Read-only listing of Kubernetes deployments. Returns desired/ready/available "
        "replica counts. NEVER modifies the cluster.\n\n"
        "Use this to find deployments that aren't fully rolled out, deployments "
        "with unavailable replicas, or to audit replica counts."
    ),
    "parameters": COMMON_PARAMS,
}

SERVICES_SCHEMA = {
    "name": "check_k8s_services",
    "description": (
        "Read-only listing of Kubernetes services. Returns type (ClusterIP, NodePort, "
        "LoadBalancer), cluster IP, ports, and selectors. NEVER modifies the cluster.\n\n"
        "Use this to discover service endpoints or audit service-to-pod mappings."
    ),
    "parameters": COMMON_PARAMS,
}

CONTEXTS_SCHEMA = {
    "name": "list_k8s_contexts",
    "description": (
        "List available kubeconfig contexts. Returns context name, cluster, user, "
        "namespace, and whether it's the current context.\n\n"
        "Call this first when the user asks about a cluster you don't know — the "
        "context name is what other K8s tools need."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# ============================================================
# Registration
# ============================================================

def _always_available() -> bool:
    return True


registry.register(
    name="check_k8s_nodes",
    toolset="k8s",
    schema=NODES_SCHEMA,
    handler=check_k8s_nodes_handler,
    check_fn=_always_available,
    emoji="🖧",
    max_result_size_chars=16000,
)

registry.register(
    name="check_k8s_pods",
    toolset="k8s",
    schema=PODS_SCHEMA,
    handler=check_k8s_pods_handler,
    check_fn=_always_available,
    emoji="📦",
    max_result_size_chars=16000,
)

registry.register(
    name="check_k8s_events",
    toolset="k8s",
    schema=EVENTS_SCHEMA,
    handler=check_k8s_events_handler,
    check_fn=_always_available,
    emoji="⚠️",
    max_result_size_chars=16000,
)

registry.register(
    name="check_k8s_deployments",
    toolset="k8s",
    schema=DEPLOYMENTS_SCHEMA,
    handler=check_k8s_deployments_handler,
    check_fn=_always_available,
    emoji="🚀",
    max_result_size_chars=16000,
)

registry.register(
    name="check_k8s_services",
    toolset="k8s",
    schema=SERVICES_SCHEMA,
    handler=check_k8s_services_handler,
    check_fn=_always_available,
    emoji="🔌",
    max_result_size_chars=16000,
)

registry.register(
    name="list_k8s_contexts",
    toolset="k8s",
    schema=CONTEXTS_SCHEMA,
    handler=list_k8s_contexts_handler,
    check_fn=_always_available,
    emoji="📋",
    max_result_size_chars=4000,
)
