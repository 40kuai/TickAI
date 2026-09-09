---
name: "diagnose_prometheus_anomaly"
description: "Read-only diagnostic skill for nfc-* services. Use whenever the user asks to check, inspect, monitor, or diagnose the health/status/anomalies/performance of any nfc service (e.g. nfc-finance, nfc-fund, nfc-user-center), or mentions monitoring/巡检/health checks for nfc. Primary method: read-only Kubernetes inspection (list_k8s_contexts, check_k8s_deployments, check_k8s_pods, check_k8s_services, check_k8s_events, check_k8s_nodes). If ARMS/Prometheus metric tools (prometheus_service_discovery, prometheus_service_health, prometheus_metric_query) are present, extend the diagnosis with them. Never uses mutating commands."
trigger: "user_initiated"
severity: "info"
---

# Read-only Diagnosis of nfc SERVICE HEALTH AND Anomalies

## Goal

When the user asks about nfc service health, status, or anomalies, use only read-only Kubernetes inspection tools to determine the state of `nfc-*` workloads and produce a structured severity-sorted report. If ARMS/Prometheus metric tools are available in the current toolset, use them to complement the Kubernetes findings with request/database/SQL/JVM/system metrics. Never modify, exec into, or write to any cluster resource.

## When to use

Use this skill for ANY request that semantically asks about nfc service health or monitoring, including:

- "看看 nfc 服务有什么异常" / "check nfc services for anomalies"
- "检查一下所有 nfc 服务的健康状态" / "check health of all nfc services"
- "nfc 系列有没有数据库/JVM/GC/CPU/内存问题"
- "nfc 某个服务是不是挂了 / 请求失败率如何"
- "nfc 服务状态怎么样 / 是否正常 / 有没有问题"
- "查询 nfc 服务的监控/指标/性能 / Prometheus 上有没有告警"
- "帮我巡检一下 nfc 服务" / "monitor nfc services"
- Any mention of "nfc", "ARMS", "Prometheus", "巡检", "健康检查", "监控巡检" in the context of checking services

Do not skip the skill because of phrasing differences (e.g. "查一下", "看下", "检测", "diagnose", "health check"). If the semantics are "nfc service health/monitoring", run this skill.

## Safety contract (read-only)

This skill is strictly read-only. Allowed tools:

- `list_k8s_contexts`
- `check_k8s_nodes`
- `check_k8s_deployments`
- `check_k8s_pods`
- `check_k8s_services`
- `check_k8s_events`
- If present in the toolset: `prometheus_service_discovery`, `prometheus_service_health`, `prometheus_metric_query`

**Forbidden:**

- Never use `kubectl exec`, `kubectl scale`, `kubectl edit`, `kubectl delete`, `kubectl patch`, `kubectl create`, or any other mutating command.
- Never attempt to run `exec`, shell, or environment inspection inside a container.
- Never guess or invent monitoring metrics or tool names that are not present.
- Never recommend the user to "go check the Prometheus console yourself" — your job is to look and report.

## Tool selection (read first)

1. Inspect your available toolset. 
2. If the three Prometheus/ARMS tools (`prometheus_service_discovery`, `prometheus_service_health`, `prometheus_metric_query`) are **present**, run the Prometheus extension described in Appendix A after completing the Kubernetes checks.
3. If they are **absent**, do **not** mention or emulate PromQL. Use only the read-only Kubernetes workflow below.

## Time window

Use a recent time window of **last 10–15 minutes** for events and status. Do not request long historical data. If the user explicitly asks for a longer window, still only use data that the tools can read without extra permissions.

## Step 0 — Context and service discovery

1. Call `list_k8s_contexts` to enumerate contexts.
2. For each **reachable** context:
   - Call `check_k8s_deployments` and `check_k8s_services`, looking for workloads whose names contain `nfc-` or whose labels include `app.kubernetes.io/name=nfc-*`, `app=nfc-*`, or similar.
   - Collect: deployment name, namespace, image, replicas; service name, type, selector, ports.
3. If no nfc-related resource is found in any reachable context, report: `"No nfc services found in the reachable contexts."` with `status: "no_data"` — do **not** say the services are healthy.

If two contexts exist and one is unreachable (e.g. connection refused), list it as `contexts_unreachable` and continue with the reachable one(s). Never block the whole diagnosis because one context is down.

## Step 1 — Deployment health

For each nfc deployment, check:

- `spec.replicas` vs `status.availableReplicas` and `status.readyReplicas`.
- `status.observedGeneration` vs `metadata.generation` (rollout progress).
- Deployment conditions (e.g. `Available`, `Progressing`).

Anomaly rules:

| Condition | Severity |
|---|---|
| `availableReplicas < spec.replicas` | `critical` if 0 ready, else `warning` |
| `observedGeneration < generation` (rollout stuck/not progressing) | `warning` |
| Deployment condition `Available=False` | `critical` if no available replicas, else `warning` |
| Deployment condition `Progressing=False` (rollout stalled) | `warning` |

Record `current_value` like `"2/3"` (ready/desired) and `expected_value` like `"3/3"`.

## Step 2 — Pod health

Call `check_k8s_pods` for nfc pods (filter by name or label, all namespaces if needed). For each pod with a name matching `nfc-*`, examine:

1. **Pod phase / container states:**
   - `CrashLoopBackOff` → `critical`
   - `ImagePullBackOff` / `ErrImagePull` → `critical`
   - `CreateContainerConfigError` or `CreateContainerError` → `critical` (likely missing ConfigMap/Secret/env)
   - `Pending` → `warning` (may be scheduling or resource related)
   - `Terminating` for more than 5 minutes → `warning`
   - `Running` but not ready → `critical` if no other ready replica, else `warning`
2. **Restart counts:** each container’s `restartCount`. Repeated restarts (>0 in the window) with last termination reason `OOMKilled` → `critical`; other repeated restarts → `warning`.
3. **Last terminated state:** record reason (Error, OOMKilled, etc.) as evidence.

For each anomalous pod, capture:
- `resource`: `namespace/pod`
- `container` name
- `current_value`: `RestartCount=5, LastState=CrashLoopBackOff`
- `expected_value`: `Running and Ready`

## Step 3 — Service / endpoint health

Call `check_k8s_services` for services named `nfc-*`. For each service:

- Check `type` (ClusterIP/NodePort/LoadBalancer).
- Check whether the service selector matches the labels on the nfc pods.
- Check for ready endpoints:
  - If the service has no ready backend endpoints while nfc pods exist but are not Ready → `critical` (no traffic path).
  - If there are ready endpoints → `normal`.
- If a LoadBalancer is used but the external IP is not assigned (`<pending>`) → `warning`.

## Step 4 — Events

Call `check_k8s_events` and filter for nfc-related resources in the last 10–15 minutes. Look for:

- `FailedScheduling` (resource shortage / node selectors / taints)
- `Unhealthy` (liveness/readiness probe failures)
- `BackOff` / `CrashLoopBackOff` (container restarts)
- `OOMKilling`
- `FailedMount` (volume/config errors)
- `Pulling` → `Pulled` (image issues)

Use the most recent events as evidence in the report. Do not report stale events outside the window.

## Step 5 — Node / system health (when relevant)

Call `check_k8s_nodes` to understand node-level context:

- `NotReady` condition → `warning`/`critical` depending on impact.
- `MemoryPressure`, `DiskPressure`, `PIDPressure` → `warning`.
- If nfc pods are `Pending` with `FailedScheduling` events, attribute the cause (resource requests vs node allocatable) to the node pressure.

This step is optional if pods/deployments already show a clear anomaly, but include it when the anomaly is `Pending` or when OOM/restarts suggest resource limits.

## Step 6 — Synthesize and report

Map findings to categories:

- `deployment_availability`
- `pod_status`
- `service_endpoints`
- `configuration`
- `resource_pressure`
- `events`

Severity assignment:

| Severity | Criteria |
|---|---|
| `critical` | CrashLoopBackOff, ImagePullBackOff, CreateContainerConfigError, OOMKilled, no available replicas, no ready endpoints, readiness failing on all replicas |
| `warning` | restarts > 0 but service still serving, rollout stuck, not-ready pod with other ready replicas, node pressure, unmounted volumes, Pending pods |
| `info` | any other noteworthy observation (e.g. image tag `latest`, single replica without HPA) |
| `normal` | only when there is actual data proving the resource is fine |
| `no_data` | empty result — never call it normal |

**Report early:** if a `critical` anomaly is found, proceed to finish a concise report as soon as the critical items are confirmed; do not run every possible check if the critical evidence is already compelling. Aim for 2–4 tool calls minimum, then produce the report.

## Output format

Return a single JSON object using lowercase keys; any free-form `message`/`suggestion` may be in the user’s language or English, but keep technical fields (`namespace`, `pod`, `resource`, `category`, `severity`) as identifiers.

```json
{
  "skill": "diagnose_prometheus_anomaly",
  "checked_at": "2026-08-26T12:00:00Z",
  "scope": {
    "contexts_checked": ["docker-desktop"],
    "contexts_unreachable": [],
    "namespaces": ["default", "nfc"],
    "nfc_services_found": ["nfc-finance", "nfc-fund", "nfc-user-center"]
  },
  "summary": {
    "total_checks": 8,
    "anomalies_found": 2,
    "severity": "critical"
  },
  "anomalies": [
    {
      "category": "pod_status",
      "check_name": "CrashLoopBackOff",
      "resource": "default/nfc-finance-5f7c9d8b6c",
      "namespace": "default",
      "pod": "nfc-finance-5f7c9d8b6c",
      "container": "app",
      "current_value": "CrashLoopBackOff, restartCount=7",
      "expected_value": "Running and Ready",
      "severity": "critical",
      "message": "nfc-finance pod enters CrashLoopBackOff with repeated restarts.",
      "suggestion": "Verify the container environment (env vars, ConfigMap, Secret) and readiness probe configuration; check startup logs if available.",
      "evidence": [
        "event: BackOff backing off restarting failed container",
        "lastState.terminated.reason=Error"
      ]
    },
    {
      "category": "deployment_availability",
      "check_name": "availableReplicas",
      "resource": "deployment/nfc-finance",
      "namespace": "default",
      "current_value": "0/1",
      "expected_value": "1/1",
      "severity": "critical",
      "message": "nfc-finance has 0 available replicas.",
      "suggestion": "Inspect the crashing container's configuration and image; check recent rollouts.",
      "evidence": ["status.availableReplicas=0"]
    }
  ],
  "normal_checks": [
    {
      "category": "deployment_availability",
      "check_name": "availableReplicas",
      "resource": "deployment/nfc-user-center",
      "namespace": "default",
      "current_value": "3/3",
      "expected_value": "3/3",
      "severity": "normal"
    }
  ],
  "no_data_checks": [
    {
      "category": "service_endpoints",
      "check_name": "endpoints_ready",
      "resource": "service/nfc-camunda-company",
      "namespace": "default",
      "current_value": "no endpoint data",
      "expected_value": "ready endpoints present",
      "severity": "no_data"
    }
  ]
}
```

If no anomalies are found and all checks have real data, set `summary.anomalies_found` to `0` and `summary.severity` to `"normal"`.

## Edge cases

- **Unreachable context:** include it in `contexts_unreachable`; do not invent data. If **no** context is reachable, return a short report stating `"All contexts unreachable"` with `no_data`.
- **No nfc resources:** return `scope.nfc_services_found: []` and `summary.severity: "info"` with message `"No nfc services found in reachable contexts"`. Do **not** call it normal.
- **Tool missing:** if a listed tool is not available, only use the available read-only tools and state which checks could not be performed. Never fabricate outputs.
- **Multiple namespaces:** always include `namespace` in `resource` and in `evidence`.
- **Only some replicas bad:** distinguish per-pod/per-node anomalies; do not mark the entire deployment critical if the service still has ready replicas.
- **Empty pod list with deployments present:** report `no_data` and suggest checking the namespace or label selector; do not say healthy.
- **Language:** Always execute the diagnostic. Never ask the user to clarify the language or to translate anything. Answer in the user’s own language if that language is other than English.

## What NOT to do

- Do **not** ask "what do you want me to translate?" or request clarification about the request language.
- Do **not** use `kubectl exec`, `logs -f`, attach, or any interactive/mutating command.
- Do **not** report "normal" for an empty result; use `no_data`.
- Do **not** run the Prometheus path if the Prometheus tools are absent.
- Do **not** invent metrics, PromQL, or `arms_*` definitions when the metric tools are unavailable.
- Do **not** run every check exhaustively if a `critical` anomaly is already confirmed — report promptly.
- Do **not** advise the user to check the console themselves; provide your own findings.

## Appendix A — Prometheus/ARMS extension (only if tools are present)

If the following tools are **actually present** in your toolset:

- `prometheus_service_discovery(service_prefix=...)`
- `prometheus_service_health(service=...)`
- `prometheus_metric_query(query=...)`

Then after the Kubernetes workflow, additionally run:

1. `prometheus_service_discovery(service_prefix="nfc-.*")` to confirm service list.
2. `prometheus_service_health(service="nfc-.*")` to fetch five dimension snapshots (request, database, SQL, JVM, system) in one call.
3. Only if an anomaly appears in the snapshot, run `prometheus_metric_query` with a PromQL string to zoom in. Default time window: last 10 minutes.

Allowed metric names (do not invent others):

- Request: `arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc`, `arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc`, `arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc`, `arms_app_requests_seconds_ign_destid_endpoint_parent_ppid_prpc_rpc`, `arms_requests_by_status_count_ign_rpc`
- Database/SQL: `arms_db_requests_count_ign_rpc`, `arms_db_requests_error_count_ign_rpc`, `arms_db_requests_slow_count_ign_rpc`, `arms_db_requests_seconds_ign_rpc`, `arms_sql_requests_count_ign_rpc`, `arms_sql_requests_error_count_ign_rpc`, `arms_sql_requests_slow_count_ign_rpc`, `arms_sql_requests_seconds_ign_rpc`, `arms_exception_requests_count_ign_destid_endpoint_rpc`, `arms_exception_requests_seconds_ign_destid_endpoint_rpc`
- JVM: `arms_jvm_gc_total`, `arms_jvm_gc_seconds_total`
- System: `arms_system_cpu_idle`, `arms_system_cpu_io_wait`, `arms_system_net_out_errs`, `arms_system_net_in_errs`

Never use `http_requests_total`, `up`, `node_*`, or any invented `arms_*` metric.

Severity mapping for metric anomalies:

| Check | Threshold | Severity |
|---|---|---|
| error request ratio | ≥ 0.10 | critical |
| HTTP 200 ratio | ≤ 0.90 | critical |
| DB latency | ≥ 5s | critical |
| DB/SQL error ratio | ≥ 0.05 | warning |
| slow SQL count > 0 | > 0 | warning |
| old GC freq | ≥ 0.05/min | warning |
| old GC duration | ≥ 3s | warning |
| IO wait | ≥ 1% | warning |
| network errors | > 0 | warning |
| young GC freq | ≥ 5/min | info |
| young GC duration | ≥ 8s | info |
| CPU idle | ≤ 20% | info |

If Prometheus data returns empty for a service, mark that service’s checks as `no_data`, not `normal`.

Merge the Kubernetes findings and Prometheus findings into one final JSON report using the output format above. The `skill` field remains `"diagnose_prometheus_anomaly"`.