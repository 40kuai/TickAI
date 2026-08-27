---
name: diagnose_prometheus_anomaly
description: >-
  通过 ARMS Prometheus 指标检查 nfc 系列服务的健康状态与运行异常，涵盖服务请求、
  数据库、SQL、JVM、系统五个维度。当用户查询/巡检/诊断任何 nfc 服务的状态、健康、
  监控、性能、异常、告警、数据库/JVM/GC/CPU 问题，或要求"检查/看一下 nfc 服务"
  时，都必须使用本 skill 来完成查询与分析。
trigger: user_initiated
severity: info
---

# 业务异常诊断（Prometheus 指标）

## Goal

当用户询问 nfc 系列服务的健康状态或异常情况时，按以下步骤依次检查各维度指标，
定位异常并给出结构化报告。**本 skill 是 nfc 服务状态/监控巡检的唯一入口。**

## When to use

**只要用户的意图涉及 nfc 系列服务的状态、健康、监控、巡检或异常诊断，就立即使用本 skill，
不要试图用其他通用工具或凭空回答。** 典型问法包括：

- 用户问："看看 nfc 服务有什么异常"
- 用户问："检查一下所有 nfc 服务的健康状态"
- 用户问："nfc 系列有没有数据库异常"
- 用户问："最近 nfc 服务有没有 GC 问题"
- 用户问："Prometheus 上 nfc 系列服务有没有告警"
- 用户问："帮我巡检一下 nfc 服务"
- 用户问："nfc 服务状态怎么样 / 是否正常 / 有没有问题"
- 用户问："查一下 nfc 服务的监控/指标/性能"
- 用户问："nfc 某个服务是不是挂了 / 请求失败率如何"
- 用户问："nfc 服务的 CPU / 内存 / JVM / GC / 慢 SQL / 网络 有没有异常"
- 用户提到 "nfc"、"ARMS"、"Prometheus"、"arms_" 前缀指标、"巡检"、"健康检查"、"监控巡检" 等关键词

**不要因为措辞变化（如"查一下"、"看下"、"检测"、"诊断"、"monitor"、"health"）而漏掉本 skill。
只要语义是"nfc 服务健康/监控"，就调用 `run_skill(skill_name="diagnose_prometheus_anomaly")`。**

## How to use the tools

Prometheus 监控查询**只有一个工具**：

**`prometheus_service_health`**（Prometheus/ARMS 监控查询唯一入口），两种模式：

1. **服务健康快照（默认）**：传 `service`（如 `"nfc-.*"` 或 `"nfc-finance"`），一次调用取回该服务的服务请求、数据库、JVM、系统四类核心指标的结构化快照。检查 nfc 服务健康状态时**必须**用这个模式。
2. **任意指标查询**：传 `query`（PromQL），执行指定指标查询（最近 10 分钟），用于快照发现异常后深入定位。

**所有 Prometheus 相关查询都走这一个工具，不要发明或想象其它查询工具。**

**重要**：PromQL 中包含引号时，需要在 JSON 字符串中用反斜杠转义，例如：
`avg(arms_db_requests_seconds_ign_rpc{service=~"nfc-.*"}) by(service)` 中的 `"nfc-.*"` 需要写作 `\"nfc-.*\"`。

**注意**：查询结果中的 `metric` 字段包含标签信息（如 `service`、`serverIp` 等），`value` 字段是当前值。

## ✅ 推荐流程（避免轮次耗尽）

1. **调用一次 `prometheus_service_health(service="nfc-.*")`** 拿全 nfc 服务的核心指标快照。
2. 只有在快照发现某个维度异常、需要深入定位时，才再调用一次 `prometheus_service_health` 并传 `query`（PromQL）补充查询。
3. 这样通常 **1-2 次工具调用** 就能完成诊断并输出报告，不会触顶 `(max tool rounds reached)`。

## ⚠️ VALID METRICS WHITELIST (READ THIS FIRST)

**CRITICAL: You MUST ONLY use these exact metric names. Do NOT invent or guess metric names.**

### Service Request Metrics (服务请求指标)
- `arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc` — 请求数
- `arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc` — 错误请求数
- `arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc` — 慢请求数
- `arms_app_requests_seconds_ign_destid_endpoint_parent_ppid_prpc_rpc` — 请求耗时
- `arms_requests_by_status_count_ign_rpc` — 分状态码请求数

### Database Metrics (数据库指标)
- `arms_db_requests_count_ign_rpc` — 数据库请求数
- `arms_db_requests_error_count_ign_rpc` — 数据库错误请求数
- `arms_db_requests_slow_count_ign_rpc` — 数据库慢请求数
- `arms_db_requests_seconds_ign_rpc` — 数据库请求耗时

### SQL Metrics (SQL指标)
- `arms_sql_requests_count_ign_rpc` — SQL请求数
- `arms_sql_requests_error_count_ign_rpc` — SQL错误请求数
- `arms_sql_requests_slow_count_ign_rpc` — SQL慢请求数
- `arms_sql_requests_seconds_ign_rpc` — SQL请求耗时
- `arms_exception_requests_count_ign_destid_endpoint_rpc` — 异常请求数
- `arms_exception_requests_seconds_ign_destid_endpoint_rpc` — 异常请求耗时

### JVM Metrics (JVM指标)
- `arms_jvm_gc_total` — 累计GC发生次数
- `arms_jvm_gc_seconds_total` — 累计GC耗时

### System Metrics (系统指标)
- `arms_system_cpu_idle` — CPU空闲占比
- `arms_system_cpu_io_wait` — IO Wait CPU占比
- `arms_system_net_out_errs` — 网络出口错误
- `arms_system_net_in_errs` — 网络入口错误

### ❌ NEVER USE THESE (FORBIDDEN)
- `http_requests_total` — 通用 Prometheus 指标，ARMS 不存在
- `arms_http_requests_count` — 不存在，正确名称是 `arms_requests_by_status_count_ign_rpc`
- `up` — 通用 Prometheus 指标，ARMS 没有基础设施可用性指标
- `node_*` — Node Exporter 指标，ARMS 不包含
- 任何你自己编造的 `arms_*` 指标名

### How to check if a service is "up"
ARMS 没有 `up` 指标。要检查服务是否存活，使用以下方法：
1. 查询 `arms_system_cpu_idle{service=~"SERVICE_FILTER"}` — 如果有数据返回，说明服务在运行
2. 查询 `arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc{service=~"SERVICE_FILTER"}` — 如果有请求数，说明服务在工作
3. 如果以上查询都返回空，说明该服务当前没有上报数据

## Service name handling

**不要依赖用户输入的服务名。** 用户可能不知道确切的服务名，或者使用了简写。

第一步永远是查询 `arms_db_requests_seconds_ign_rpc{service=~"nfc-.*"}` 来获取所有有数据库请求的 nfc 服务名列表。
然后使用这个列表作为基准，在所有后续检查中只查询这些已知存在的服务。

如果有服务只有系统指标（CPU、JVM）但没有数据库指标，它不会出现在这个列表中。
这种情况下可以先用 `arms_system_cpu_idle{service=~"nfc-.*"}` 兜底获取服务列表。

## Checking steps

按以下顺序依次检查，发现异常立即记录，继续下一个检查。

**首先调用一次 `prometheus_service_health(service="nfc-.*")` 获取全部核心指标快照，
然后基于快照结果按下述维度分析。只有快照显示某维度有异常时，才再次调用
`prometheus_service_health` 并传 `query`（PromQL）深入定位。**

---

## ⏱️ 默认时间范围与查询性能（重要，先读）

- **默认只查最近 10 分钟的数据。** `prometheus_service_health` 不传 `start`/`end` 时自动用最近 10 分钟。除非用户明确要求更长时间，否则**不要**指定更大的时间范围。
- **控制查询数量，减少轮次：**
  - 快照模式一次调用即可覆盖全部维度，不要逐个指标查询。
  - 只有快照发现异常时才做深入查询；发现异常立即报告，不要等全部跑完。
  - 不要对同一数据重复查询。
- **发现首个关键异常（critical）就直接汇总报告**，不要继续跑完所有检查。
- 若连续多次查询返回空/超时，停止查询并如实说明"数据不可得"，不要反复重试。

---

### Step 0: 发现服务

在所有检查之前，先查询 `arms_db_requests_seconds_ign_rpc{service=~"nfc-.*"}`，
从返回结果中提取所有 `service` 标签值，作为后续检查的服务列表基础。

**PromQL:**
```
avg(arms_db_requests_seconds_ign_rpc{service=~"nfc-.*"}) by(service)
```

如果结果为空，说明数据库指标可能没有数据，改用以下查询兜底获取服务列表：
```
avg(arms_system_cpu_idle{service=~"nfc-.*"}) by(service)
```

从结果中提取服务名列表，例如：`["nfc-finance", "nfc-fund", "nfc-user-center", ...]`。

后续所有检查的 `SERVICE_FILTER` 统一使用 `nfc-.*` 通配，不需要逐个服务查询。
如果查询结果中某个服务有数据而其他服务没有，属于正常现象（不同服务可能上报不同指标）。

---

### Step 1: 服务请求异常

#### 1.1 服务错误请求百分比

**PromQL:**
```
sum(sum_over_time_lorc(arms_app_requests_error_count_ign_destid_endpoint_parent_ppid_prpc_rpc{service=~"SERVICE_FILTER",callKind=~"http|rpc|consumer|custom_entry|server"}[1m])) by(service,callKind) / sum(sum_over_time_lorc(arms_app_requests_count_ign_destid_endpoint_parent_ppid_prpc_rpc{service=~"SERVICE_FILTER",callKind=~"http|rpc|consumer|custom_entry|server"}[1m])) by(service,callKind)
```

**阈值：** >= 0.10（10%）

**说明：** 检查各服务 + 调用类型的错误请求占比。超过 10% 视为异常。

#### 1.2 HTTP 状态码 200 占比

**PromQL:**
```
sum(arms_requests_by_status_count_ign_rpc{service=~"SERVICE_FILTER",status="200"}) by(service) / sum(arms_requests_by_status_count_ign_rpc{service=~"SERVICE_FILTER"}) by(service)
```

**阈值：** <= 0.90（90%）

**说明：** HTTP 请求中 200 状态码比例低于 90% 说明异常响应增多。

#### 1.3 慢请求数

**PromQL:**
```
sum(arms_app_requests_slow_count_ign_destid_endpoint_parent_ppid_prpc_rpc{service=~"SERVICE_FILTER"}) by(service)
```

**说明：** 检查慢请求的绝对数量。如果 > 0 且数量较大，说明服务存在性能瓶颈。

---

### Step 2: 数据库异常

#### 2.1 数据库请求耗时

**PromQL:**
```
avg(arms_db_requests_seconds_ign_rpc{service=~"SERVICE_FILTER"}) by(service, callKind, endpoint, destId)
```

**阈值：** >= 5（秒）

**说明：** 数据库平均请求耗时超过 5 秒视为异常。检查具体是哪个 endpoint 和 destId。

#### 2.2 数据库错误百分比

**PromQL:**
```
sum(arms_sql_requests_error_count_ign_rpc{service=~"SERVICE_FILTER"}) by(service, callKind, endpoint) / sum(arms_sql_requests_count_ign_rpc{service=~"SERVICE_FILTER"}) by(service, callKind, endpoint)
```

**阈值：** >= 0.05（5%）

#### 2.3 SQL 慢请求

**PromQL:**
```
sum(arms_sql_requests_slow_count_ign_rpc{service=~"SERVICE_FILTER"}) by(service, endpoint, destId)
```

**说明：** 慢 SQL 数量 > 0 说明需要关注 SQL 执行效率。

---

### Step 3: JVM 异常

#### 3.1 Young GC 频率

**PromQL:**
```
avg(irate(arms_jvm_gc_total{service=~"SERVICE_FILTER",gen="young"}[1m])) by(service, serverIp)
```

**阈值：** >= 5（次/分钟）

**说明：** Young GC 超过 5 次/分钟可能说明对象分配过快或新生代偏小。

#### 3.2 Old GC 频率

**PromQL:**
```
avg(irate(arms_jvm_gc_total{service=~"SERVICE_FILTER",gen="old"}[1m])) by(service, serverIp)
```

**阈值：** >= 0.05（次/分钟）

**说明：** Old GC 超过 0.05 次/分钟（即每 20 分钟 1 次以上）说明老年代增长过快。

#### 3.3 Young GC 耗时

**PromQL:**
```
avg(increase(arms_jvm_gc_seconds_total{service=~"SERVICE_FILTER",gen="young"}[1m])) by(service, serverIp)
```

**阈值：** >= 8（秒）

**说明：** Young GC 每分钟耗时超过 8 秒说明 GC 停顿时间过长。

#### 3.4 Old GC 耗时

**PromQL:**
```
avg(increase(arms_jvm_gc_seconds_total{service=~"SERVICE_FILTER",gen="old"}[1m])) by(service, serverIp)
```

**阈值：** >= 3（秒）

**说明：** Old GC 每分钟耗时超过 3 秒说明 Full GC 停顿严重。

#### 3.5 异常请求数

**PromQL:**
```
sum(arms_exception_requests_count_ign_destid_endpoint_rpc{service=~"SERVICE_FILTER"}) by(service, endpoint)
```

**说明：** 检查服务抛出的异常数量。如果 > 0，说明代码存在未捕获异常。

---

### Step 4: 系统异常

#### 4.1 CPU 空闲占比

**PromQL:**
```
avg(arms_system_cpu_idle{service=~"SERVICE_FILTER"}) by(service)
```

**阈值：** <= 20（%）

**说明：** CPU 空闲率低于 20% 说明 CPU 资源紧张。

#### 4.2 IO Wait CPU 占比

**PromQL:**
```
avg(arms_system_cpu_io_wait{service=~"SERVICE_FILTER"}) by(service)
```

**阈值：** >= 1（%）

**说明：** IO Wait 超过 1% 说明磁盘 IO 可能存在瓶颈。

#### 4.3 网络出口错误

**PromQL:**
```
sum(arms_system_net_out_errs{service=~"SERVICE_FILTER"}) by(service)
```

**阈值：** > 0

**说明：** 网络出口错误 > 0 说明服务出方向网络存在问题。

#### 4.4 网络入口错误

**PromQL:**
```
sum(arms_system_net_in_errs{service=~"SERVICE_FILTER"}) by(service)
```

**阈值：** > 0

**说明：** 网络入口错误 > 0 说明服务入方向网络存在问题。

---

## Output format

将检查结果组织为结构化报告，按严重程度排序输出。

```json
{
  "service": "nfc-finance",
  "checked_at": "2026-07-23T10:00:00Z",
  "summary": {
    "total_checks": 12,
    "anomalies_found": 3,
    "severity": "warning"
  },
  "anomalies": [
    {
      "category": "service_request",
      "check_name": "错误请求百分比",
      "service": "nfc-finance",
      "call_kind": "http",
      "current_value": 0.15,
      "threshold": 0.10,
      "unit": "percent(0.0-1.0)",
      "severity": "critical",
      "message": "nfc-finance 服务 HTTP 错误请求占比 15%，超过阈值 10%",
      "suggestion": "检查最近是否有代码变更上线，查看对应 endpoint 的日志定位错误原因"
    }
  ],
  "normal_checks": [
    {
      "category": "jvm",
      "check_name": "Young GC 频率",
      "service": "nfc-finance",
      "current_value": 2.1,
      "threshold": 5.0,
      "unit": "次/分钟",
      "status": "normal"
    }
  ]
}
```

### 严重级别定义

| 级别 | 说明 |
|------|------|
| `critical` | 正在影响用户或有资金损失风险，需要立即处理 |
| `warning` | 指标异常但尚未影响用户，需要关注 |
| `info` | 仅作信息参考，无需立即处理 |
| `normal` | 指标正常 |

### 严重级别分配规则

- **critical**: 服务错误请求百分比 >= 10%、数据库耗时 >= 5s、HTTP 200 占比 <= 90%
- **warning**: Old GC 频率/耗时异常、IO Wait 异常、网络错误
- **info**: Young GC 频率/耗时异常、CPU 空闲低
- **normal**: 所有指标在阈值范围内

## Edge cases

- **指标无数据（空结果）：** 标记为 `status: "no_data"`，说明可能是服务刚部署或指标采集延迟，不要误报为正常
- **Step 0 发现服务为空：** 如果 `arms_db_requests_seconds_ign_rpc` 和 `arms_system_cpu_idle` 都返回空，说明当前 Prometheus 实例中没有 nfc 系列服务数据，直接返回"未发现 nfc 服务数据"
- **多个 serverIp 的结果：** 对于 JVM 和系统指标，同一个服务可能有多个实例（serverIp）。如果某一台异常而其他正常，应单独指出具体 IP
- **查询超时：** 复杂 PromQL 可能超时。如果遇到超时可尝试缩小时间范围（默认最近 10 分钟即可）或简化查询条件。若同一查询连续超时/失败 2 次以上，跳过该检查并如实说明，不要反复重试。
- **查询报错/返回空：** 单个查询失败不要中止整个 skill，先记录，继续下一个维度；但不要对同一个指标反复重试。
- **结果被截断：** 工具返回的结果如果超过 20 条会被截断。如果发现 `truncated: true`，应缩小查询范围重新查询

## What NOT to do

- **不要** 对空结果下结论说"指标正常" —— 空结果可能是无数据，而不是正常
- **不要** 一次性把所有查询跑完 —— 发现一个异常就先报告，不要让用户等
- **不要** 修改 PromQL 模板中的指标名 —— 指标名是固定的，只能修改 service 过滤条件
- **不要** 建议用户去 Prometheus 页面查看 —— 你的任务就是代替用户查询和分析
- **不要** 对每个指标都做范围查询 —— 优先用即时查询，只在需要看趋势时才用范围查询
- **不要** 为了"完整"跑完所有检查而反复查询 —— 目标是在最近 10 分钟内快速定位异常；每维度最多 1 条查询，发现 critical 即报告