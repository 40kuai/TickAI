---
name: "diagnose_prometheus_anomaly"
description: "nfc-* 系列服务的只读诊断技能。当用户查询/巡检/诊断任何 nfc 服务（如 nfc-finance、nfc-fund、nfc-user-center）的健康状态、运行异常、监控、性能，或提到监控/巡检/健康检查时使用。主要方法：只读 Kubernetes 巡检（list_k8s_contexts、check_k8s_deployments、check_k8s_pods、check_k8s_services、check_k8s_events、check_k8s_nodes）。若当前工具集中存在 ARMS/Prometheus 指标工具（prometheus_service_discovery、prometheus_service_health、prometheus_metric_query），则用它们补充诊断。禁止使用任何变更类命令。"
trigger: "user_initiated"
severity: "info"
---

# nfc 服务健康与异常只读诊断

## 目标

当用户询问 nfc 服务健康、状态或异常时，仅使用只读的 Kubernetes 巡检工具判断 `nfc-*` 负载的状态，并产出按严重程度排序的结构化报告。如果当前工具集中存在 ARMS/Prometheus 指标工具，用它们以请求/数据库/SQL/JVM/系统指标补充 Kubernetes 结论。绝不修改、exec 或写入任何集群资源。

## 使用时机

本技能适用于任何语义上询问 nfc 服务健康或监控的请求，包括但不限于：

- "看看 nfc 服务有什么异常" / "check nfc services for anomalies"
- "检查一下所有 nfc 服务的健康状态" / "check health of all nfc services"
- "nfc 系列有没有数据库/JVM/GC/CPU/内存问题"
- "nfc 某个服务是不是挂了 / 请求失败率如何"
- "nfc 服务状态怎么样 / 是否正常 / 有没有问题"
- "查询 nfc 服务的监控/指标/性能 / Prometheus 上有没有告警"
- "帮我巡检一下 nfc 服务" / "monitor nfc services"
- 任何在"检查服务"语境下提到 "nfc"、"ARMS"、"Prometheus"、"巡检"、"健康检查"、"监控巡检" 的请求

不要因为措辞差异（如"查一下"、"看下"、"检测"、"diagnose"、"health check"）就跳过本技能。只要语义是"nfc 服务健康/监控"，就执行本技能。

## 安全契约（只读）

本技能严格只读。允许的工具：

- `list_k8s_contexts`
- `check_k8s_nodes`
- `check_k8s_deployments`
- `check_k8s_pods`
- `check_k8s_services`
- `check_k8s_events`
- 若工具集中存在：`prometheus_service_discovery`、`prometheus_service_health`、`prometheus_metric_query`

**禁止：**

- 绝不可使用 `kubectl exec`、`kubectl scale`、`kubectl edit`、`kubectl delete`、`kubectl patch`、`kubectl create` 或任何其他变更类命令。
- 绝不可尝试在容器内运行 exec、shell 或环境检查。
- 绝不可臆造不存在或不在工具集中的监控指标或工具名。
- 绝不可建议用户"自己去 Prometheus 控制台看"——你的职责是查看并汇报。

## 工具选择（先读此节）

1. 检查当前可用工具集。
2. 若三个 Prometheus/ARMS 工具（`prometheus_service_discovery`、`prometheus_service_health`、`prometheus_metric_query`）**存在**，在完成 Kubernetes 检查后执行附录 A 描述的 Prometheus 扩展流程。
3. 若它们**不存在**，不要提及或模拟 PromQL，只使用下面的只读 Kubernetes 流程。

## 时间窗口

事件与状态使用最近 **10–15 分钟**的时间窗口。不要请求长历史数据。若用户明确要求更长窗口，仍只使用工具无需额外权限即可读取的数据。

## 步骤 0 — 上下文与服务发现

1. 调用 `list_k8s_contexts` 枚举上下文。
2. 对每个**可达**的上下文：
   - 调用 `check_k8s_deployments` 和 `check_k8s_services`，查找名称包含 `nfc-` 或标签包含 `app.kubernetes.io/name=nfc-*`、`app=nfc-*` 之类的负载。
   - 收集：deployment 名称、命名空间、镜像、副本数；service 名称、类型、selector、端口。
3. 若任何可达上下文中都找不到 nfc 相关资源，报告：`"No nfc services found in the reachable contexts."`，`status: "no_data"` —— **不要**说服务是健康的。

若存在两个上下文且其中一个不可达（如连接拒绝），将其列入 `contexts_unreachable` 并继续检查可达的上下文。绝不让单个上下文故障阻塞整个诊断。

## 步骤 1 — Deployment 健康

对每个 nfc deployment，检查：

- `spec.replicas` 对比 `status.availableReplicas` 与 `status.readyReplicas`。
- `status.observedGeneration` 对比 `metadata.generation`（滚动发布进度）。
- Deployment 条件（如 `Available`、`Progressing`）。

异常规则：

| 条件 | 严重程度 |
|---|---|
| `availableReplicas < spec.replicas` | 0 就绪则 `critical`，否则 `warning` |
| `observedGeneration < generation`（滚动发布卡住/未推进） | `warning` |
| Deployment 条件 `Available=False` | 无可用副本则 `critical`，否则 `warning` |
| Deployment 条件 `Progressing=False`（滚动发布停滞） | `warning` |

`current_value` 记录如 `"2/3"`（就绪/期望），`expected_value` 记录如 `"3/3"`。

## 步骤 2 — Pod 健康

对 nfc pod（按名称或标签过滤，必要时全命名空间）调用 `check_k8s_pods`。对每个名称匹配 `nfc-*` 的 pod，检查：

1. **Pod 阶段 / 容器状态：**
   - `CrashLoopBackOff` → `critical`
   - `ImagePullBackOff` / `ErrImagePull` → `critical`
   - `CreateContainerConfigError` 或 `CreateContainerError` → `critical`（可能是 ConfigMap/Secret/env 缺失）
   - `Pending` → `warning`（可能与调度或资源有关）
   - `Terminating` 超过 5 分钟 → `warning`
   - `Running` 但未就绪 → 无其他就绪副本则 `critical`，否则 `warning`
2. **重启次数：** 每个容器的 `restartCount`。窗口内重复重启（>0）且最近终止原因为 `OOMKilled` → `critical`；其他重复重启 → `warning`。
3. **最近终止状态：** 记录原因（Error、OOMKilled 等）作为证据。

对每个异常 pod，记录：
- `resource`：`namespace/pod`
- `container` 名称
- `current_value`：`RestartCount=5, LastState=CrashLoopBackOff`
- `expected_value`：`Running and Ready`

## 步骤 3 — Service / 端点健康

对名称匹配 `nfc-*` 的 service 调用 `check_k8s_services`。对每个 service：

- 检查 `type`（ClusterIP/NodePort/LoadBalancer）。
- 检查 service selector 是否匹配 nfc pod 的标签。
- 检查就绪端点：
  - service 无就绪后端端点，但 nfc pod 存在且未就绪 → `critical`（无流量路径）。
  - 存在就绪端点 → `normal`。
- 使用 LoadBalancer 但外部 IP 未分配（`<pending>`）→ `warning`。

## 步骤 4 — 事件

调用 `check_k8s_events` 并过滤最近 10–15 分钟内 nfc 相关资源的事件。关注：

- `FailedScheduling`（资源不足 / 节点选择器 / 污点）
- `Unhealthy`（存活/就绪探针失败）
- `BackOff` / `CrashLoopBackOff`（容器重启）
- `OOMKilling`
- `FailedMount`（卷/配置错误）
- `Pulling` → `Pulled`（镜像问题）

报告中使用最近的事件作为证据。不要报告窗口之外的历史事件。

## 步骤 5 — 节点 / 系统健康（相关时）

调用 `check_k8s_nodes` 了解节点级上下文：

- `NotReady` 条件 → 视影响为 `warning`/`critical`。
- `MemoryPressure`、`DiskPressure`、`PIDPressure` → `warning`。
- 若 nfc pod 处于 `Pending` 且有 `FailedScheduling` 事件，将原因（资源请求 vs 节点可分配）归于节点压力。

若 pod/deployment 已明确显示异常，本步骤可选，但异常为 `Pending` 或重启/OOM 疑似资源限制时必须包含本步骤。

## 步骤 6 — 汇总与报告

将发现映射到分类：

- `deployment_availability`
- `pod_status`
- `service_endpoints`
- `configuration`
- `resource_pressure`
- `events`

严重程度分配：

| 严重程度 | 判定标准 |
|---|---|
| `critical` | CrashLoopBackOff、ImagePullBackOff、CreateContainerConfigError、OOMKilled、无可用副本、无就绪端点、全部副本就绪探针失败 |
| `warning` | 重启 > 0 但服务仍在提供流量、滚动发布停滞、有其他就绪副本的未就绪 pod、节点压力、卷未挂载、Pending pod |
| `info` | 其他值得注意的观察（如镜像 tag `latest`、无 HPA 的单副本） |
| `normal` | 仅当有实际数据证明资源正常时才可用 |
| `no_data` | 空结果——绝不称之为 normal |

**尽早报告：** 若发现 `critical` 异常，确认关键项后尽快输出精简报告；关键证据已充分时不必跑完所有检查。最少 2–4 次工具调用后即可产出报告。

## 输出格式

返回单个 JSON 对象，使用小写键；`message`/`suggestion` 等自由文本可用用户语言或英文，但技术字段（`namespace`、`pod`、`resource`、`category`、`severity`）作为标识符保留。

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
      "message": "nfc-finance pod 持续 CrashLoopBackOff 并多次重启。",
      "suggestion": "检查容器环境（env 变量、ConfigMap、Secret）与就绪探针配置；有启动日志时查看启动日志。",
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
      "message": "nfc-finance 可用副本数为 0。",
      "suggestion": "检查崩溃容器的配置与镜像；检查最近的发布记录。",
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

若未发现异常且所有检查都有真实数据，设置 `summary.anomalies_found` 为 `0`、`summary.severity` 为 `"normal"`。

## 边界情况

- **上下文不可达：** 将其列入 `contexts_unreachable`；不要臆造数据。若**所有**上下文均不可达，返回简短报告，说明 `"All contexts unreachable"`，`no_data`。
- **无 nfc 资源：** 返回 `scope.nfc_services_found: []`，`summary.severity: "info"`，message 为 `"No nfc services found in reachable contexts"`。**不要**称之为 normal。
- **工具缺失：** 若列出的工具不可用，只使用可用的只读工具，并说明哪些检查未能执行。绝不虚构输出。
- **多命名空间：** 始终在 `resource` 和 `evidence` 中包含 `namespace`。
- **部分副本异常：** 区分单个 pod/节点的异常；服务仍有就绪副本时，不要把整个 deployment 标记为 critical。
- **存在 deployment 但 pod 列表为空：** 报告 `no_data`，建议检查命名空间或标签选择器；不要说健康。
- **语言：** 始终执行诊断。绝不让用户澄清语言或翻译任何内容。用用户自己的语言回答（若该语言非中文）。

## 禁止事项

- **不要**问"您要我翻译什么？"或请求澄清请求语言。
- **不要**使用 `kubectl exec`、`logs -f`、attach 或任何交互式/变更类命令。
- **不要**对空结果报告 "normal"，使用 `no_data`。
- **不要**在 Prometheus 工具缺失时执行 Prometheus 路径。
- **不要**在指标工具不可用时臆造指标、PromQL 或 `arms_*` 定义。
- **不要**在已确认 `critical` 异常时仍穷尽式跑完所有检查——及时报告。
- **不要**建议用户自己去控制台查看；提供你自己的发现。

## 附录 A — Prometheus/ARMS 扩展（仅当工具存在时）

若以下工具**确实**存在于工具集中：

- `prometheus_service_discovery(service_prefix=...)`
- `prometheus_service_health(service=...)`
- `prometheus_metric_query(query=...)`

则在 Kubernetes 流程之后，额外执行：

1. `prometheus_service_discovery(service_prefix="nfc-.*")` 确认服务列表。
2. `prometheus_service_health(service="nfc-.*")` 一次调用获取五个维度快照（request、database、SQL、JVM、system）。
3. 仅当快照中出现异常时，用 `prometheus_metric_query` 传入 PromQL 深入定位。默认时间窗口：最近 10 分钟。

允许的指标名（不要臆造其他指标）：

- 请求：`arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_app_requests_seconds_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_requests_by_status_count_ign_rpc`
- 数据库/SQL：`arms_db_requests_count_ign_rpc`、`arms_db_requests_error_count_ign_rpc`、`arms_db_requests_slow_count_ign_rpc`、`arms_db_requests_seconds_ign_rpc`、`arms_sql_requests_count_ign_rpc`、`arms_sql_requests_error_count_ign_rpc`、`arms_sql_requests_slow_count_ign_rpc`、`arms_sql_requests_seconds_ign_rpc`、`arms_exception_requests_count_ign_destid_endpoint_rpc`、`arms_exception_requests_seconds_ign_destid_endpoint_rpc`
- JVM：`arms_jvm_gc_total`、`arms_jvm_gc_seconds_total`
- 系统：`arms_system_cpu_idle`、`arms_system_cpu_io_wait`、`arms_system_net_out_errs`、`arms_system_net_in_errs`

绝不使用 `http_requests_total`、`up`、`node_*` 或任何臆造的 `arms_*` 指标。

指标异常的严重程度映射：

| 检查 | 阈值 | 严重程度 |
|---|---|---|
| 请求错误率 | ≥ 0.10 | critical |
| HTTP 200 占比 | ≤ 0.90 | critical |
| DB 延迟 | ≥ 5s | critical |
| DB/SQL 错误率 | ≥ 0.05 | warning |
| 慢 SQL 数量 > 0 | > 0 | warning |
| 老年代 GC 频率 | ≥ 0.05/min | warning |
| 老年代 GC 时长 | ≥ 3s | warning |
| IO 等待 | ≥ 1% | warning |
| 网络错误 | > 0 | warning |
| 新生代 GC 频率 | ≥ 5/min | info |
| 新生代 GC 时长 | ≥ 8s | info |
| CPU 空闲 | ≤ 20% | info |

若某服务的 Prometheus 数据返回空，将该服务的检查标记为 `no_data`，而不是 `normal`。

将 Kubernetes 结论与 Prometheus 结论合并为一个最终 JSON 报告，使用上面的输出格式。`skill` 字段保持 `"diagnose_prometheus_anomaly"`。
