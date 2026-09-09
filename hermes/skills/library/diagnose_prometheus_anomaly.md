---
name: "diagnose_prometheus_anomaly"
description: "nfc-* 系列服务的只读 Prometheus/ARMS 指标诊断技能。当用户查询/巡检/诊断任何 nfc 服务（如 nfc-finance、nfc-fund、nfc-user-center）的监控指标、请求错误、慢请求、数据库/SQL 异常、JVM/GC、系统资源问题，或提到 Prometheus/ARMS/指标/告警/性能分析时使用。方法：prometheus_service_discovery 确认服务，prometheus_service_health 获取五维指标快照（请求/数据库/SQL/JVM/系统），prometheus_metric_query 深入定位异常。禁止使用任何变更类命令或非 Prometheus 工具。"
trigger: "user_initiated"
severity: "info"
---

# nfc 服务 Prometheus/ARMS 指标异常诊断

## 目标

当用户询问 nfc 服务监控/指标/性能/异常时，仅使用三个只读 Prometheus/ARMS 指标工具，从请求、数据库、SQL、JVM、系统五个维度判断 nfc-* 服务是否有指标异常，并产出按严重程度排序的结构化报告。绝不修改、写入或部署任何资源。

## 使用时机

本技能适用于任何语义上询问 nfc 服务监控/指标/性能的请求，包括但不限于：

- "nfc 服务有没有请求失败/错误率升高"
- "nfc 服务数据库/SQL 慢查询情况如何"
- "nfc 服务 JVM/GC 有没有问题"
- "nfc 服务 CPU/内存/IO 异常"
- "查一下 nfc 服务的 Prometheus/ARMS 指标"
- "nfc 服务有什么告警/异常指标"
- "nfc 服务性能怎么样 / 指标健康吗"
- "巡检 nfc 服务的监控指标"

不要因为措辞差异（"查一下"、"看下"、"检测"、"诊断"）就跳过本技能。只要语义是"nfc 服务 Prometheus/ARMS 指标/监控/性能诊断"，就执行本技能。

## 安全契约（只读）

本技能严格只读，**只使用以下三个工具**：

- `prometheus_service_discovery(service_prefix=...)`
- `prometheus_service_health(service=...)`
- `prometheus_metric_query(query=...)`

**禁止：**

- 绝不可使用任何非 Prometheus/ARMS 的只读或变更类工具。
- 绝不可臆造工具、PromQL 或 `arms_*` 指标名——只使用下方"允许的指标名"列表中的指标。
- 绝不可建议用户"自己去控制台看"——你的职责是查看并汇报。

## 工具选择

先检查当前可用工具集：

1. 若 `prometheus_service_discovery`、`prometheus_service_health`、`prometheus_metric_query` 三个工具均存在，执行下方流程。
2. 若其中某个工具缺失，只使用可用的只读工具完成能完成的检查，并在报告中说明哪些检查未能执行。绝不虚构输出。

## 时间窗口

指标查询默认使用最近 **10–15 分钟**窗口，与 ARMS 告警评估周期对齐。若用户明确要求更长窗口，按用户要求调整。

## 步骤 1 — 服务发现

调用 `prometheus_service_discovery(service_prefix="nfc-.*")` 枚举 nfc 服务列表。

- 若发现 0 个服务：报告 `status: "no_data"`，message 为 `"No nfc services discovered in Prometheus"`。**不要**说服务健康。
- 记录服务清单，供后续健康快照使用。

## 步骤 2 — 健康快照（五个维度）

对发现的每个 nfc 服务调用 `prometheus_service_health(service="nfc-.*")`（一次调用覆盖全部 nfc 服务）获取五维指标快照：

1. **request**：请求量、错误数、慢请求数、响应时间
2. **database**：DB 请求量、错误数、慢请求数、响应时间
3. **sql**：SQL 请求量、错误数、慢请求数、响应时间
4. **jvm**：GC 次数与耗时
5. **system**：CPU 空闲/IO 等待、网络错误包

对照"指标异常严重程度映射"表逐项检查快照。**只有快照中出现异常时**，才进入步骤 3 用 PromQL 深入定位；无异常则直接进入步骤 4 汇总。

## 步骤 3 — 深入定位（仅异常时）

针对异常维度，用 `prometheus_metric_query(query=...)` 传入 PromQL 精确定位，例如：

- 请求错误率：`sum(rate(arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc{service="nfc-*"}[5m])) / sum(rate(arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc{service="nfc-*"}[5m]))`
- 慢 SQL：`sum(rate(arms_sql_requests_slow_count_ign_rpc{service="nfc-*"}[5m]))`
- 老年代 GC 耗时：`sum(rate(arms_jvm_gc_seconds_total{service="nfc-*"}[5m]))`
- 数据库延迟：`histogram_quantile(0.95, sum(rate(arms_db_requests_seconds_ign_rpc{service="nfc-*"}[5m])) by (le))`

**只允许使用以下指标名**（其余一律视为臆造）：

- 请求：`arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_app_requests_seconds_ign_destid_endpoint_parent_ppid_prpc_rpc`、`arms_requests_by_status_count_ign_rpc`
- 数据库/SQL：`arms_db_requests_count_ign_rpc`、`arms_db_requests_error_count_ign_rpc`、`arms_db_requests_slow_count_ign_rpc`、`arms_db_requests_seconds_ign_rpc`、`arms_sql_requests_count_ign_rpc`、`arms_sql_requests_error_count_ign_rpc`、`arms_sql_requests_slow_count_ign_rpc`、`arms_sql_requests_seconds_ign_rpc`、`arms_exception_requests_count_ign_destid_endpoint_rpc`、`arms_exception_requests_seconds_ign_destid_endpoint_rpc`
- JVM：`arms_jvm_gc_total`、`arms_jvm_gc_seconds_total`
- 系统：`arms_system_cpu_idle`、`arms_system_cpu_io_wait`、`arms_system_net_out_errs`、`arms_system_net_in_errs`

绝不使用 `http_requests_total`、`up`、`node_*` 或任何未列出的 `arms_*` 指标。

## 指标异常严重程度映射

| 维度 | 检查 | 阈值 | 严重程度 |
|---|---|---|---|
| 请求 | 请求错误率 | ≥ 0.10 | critical |
| 请求 | HTTP 200 占比 | ≤ 0.90 | critical |
| 数据库 | DB 延迟(P95) | ≥ 5s | critical |
| 数据库/SQL | DB/SQL 错误率 | ≥ 0.05 | warning |
| SQL | 慢 SQL 数量 | > 0 | warning |
| JVM | 老年代 GC 频率 | ≥ 0.05/min | warning |
| JVM | 老年代 GC 耗时 | ≥ 3s | warning |
| 系统 | IO 等待 | ≥ 1% | warning |
| 系统 | 网络错误包 | > 0 | warning |
| JVM | 新生代 GC 频率 | ≥ 5/min | info |
| JVM | 新生代 GC 耗时 | ≥ 8s | info |
| 系统 | CPU 空闲 | ≤ 20% | info |

某服务指标数据为空时，将该服务对应检查标记为 `no_data`，**不是** `normal`。

## 步骤 4 — 汇总与报告

将发现按严重程度排序输出。严重程度分配：

- `critical`：请求错误率 ≥10%、HTTP 200 ≤90%、DB 延迟 ≥5s
- `warning`：DB/SQL 错误率 ≥5%、慢 SQL>0、老年代 GC 异常、IO 等待、网络错误
- `info`：新生代 GC 略高、CPU 空闲偏低、其他值得注意的观察
- `normal`：仅当有真实数据证明指标健康时才可用
- `no_data`：空结果——绝不称之为 normal

**尽早报告：** 若发现 `critical` 异常，确认关键项后尽快输出精简报告，不必等全部检查完成。关键证据已充分时 2–4 次工具调用后即可产出报告。

## 输出格式

返回单个 JSON 对象，使用小写键；`message`/`suggestion` 等自由文本用用户语言（默认中文），技术字段（`service`、`metric`、`resource`、`category`、`severity`）作为标识符保留。

```json
{
  "skill": "diagnose_prometheus_anomaly",
  "checked_at": "2026-08-26T12:00:00Z",
  "scope": {
    "services_discovered": ["nfc-finance", "nfc-fund", "nfc-user-center"],
    "window": "10m"
  },
  "summary": {
    "total_checks": 6,
    "anomalies_found": 2,
    "severity": "critical"
  },
  "anomalies": [
    {
      "category": "request",
      "check_name": "request_error_rate",
      "resource": "nfc-finance",
      "current_value": "0.15 (15%)",
      "expected_value": "< 0.10",
      "severity": "critical",
      "message": "nfc-finance 请求错误率 15% 超过阈值。",
      "suggestion": "检查最近发布版本与上游依赖；查看异常堆栈定位错误类型。",
      "evidence": ["arms_app_requests_error_count: 150/min", "arms_app_requests_count: 1000/min"]
    },
    {
      "category": "sql",
      "check_name": "slow_sql_count",
      "resource": "nfc-fund",
      "current_value": "3 slow queries in 10m",
      "expected_value": "0",
      "severity": "warning",
      "message": "nfc-fund 出现 3 条慢 SQL。",
      "suggestion": "定位慢 SQL 语句并检查索引与数据量。",
      "evidence": ["arms_sql_requests_slow_count: 3"]
    }
  ],
  "normal_checks": [
    {
      "category": "jvm",
      "check_name": "old_gen_gc_frequency",
      "resource": "nfc-user-center",
      "current_value": "0.01/min",
      "expected_value": "< 0.05/min",
      "severity": "normal"
    }
  ],
  "no_data_checks": [
    {
      "category": "system",
      "check_name": "net_errors",
      "resource": "nfc-camunda-company",
      "current_value": "no metric data",
      "expected_value": "metric data present",
      "severity": "no_data"
    }
  ]
}
```

若未发现异常且所有检查都有真实数据，设置 `summary.anomalies_found` 为 `0`、`summary.severity` 为 `"normal"`。

## 边界情况

- **服务发现为空**：`scope.services_discovered: []`，`summary.severity: "info"`，message 为 `"No nfc services discovered in Prometheus"`。**不要**称之为 normal。
- **某服务无指标数据**：该服务检查标记 `no_data`，不臆造数值。
- **工具缺失**：说明哪些检查未能执行，只汇报已完成的检查。
- **PromQL 无返回**：标记 `no_data`，说明可能服务已下线或指标采集中断。
- **语言**：始终执行诊断，用用户自己的语言回答（非中文时按用户语言）。

## 禁止事项

- **不要**使用任何非 Prometheus/ARMS 工具——本技能只允许三个 Prometheus 指标工具。
- **不要**臆造指标名、PromQL 或 `arms_*` 定义——只使用允许列表。
- **不要**对空结果报告 "normal"，使用 `no_data`。
- **不要**在已确认 `critical` 异常时仍穷尽式跑完所有检查——及时报告。
- **不要**建议用户自己去控制台查看；提供你自己的发现。
