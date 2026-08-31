---
name: diagnose_alert_root_cause
description: >-
  以 Nightingale 告警为入口的故障闭环排查技能。当 nfc 等服务出现告警、异常、
  故障、可用性下降，或用户要求"查告警/看告警/分析告警/排查某某服务的告警/
  为什么告警/怎么处理告警"时使用。串起告警拉取 - 多指标分析 - 根因输出 - 自愈建议
  完整处置流程。若用户只问监控健康巡检（无告警语义），改用 diagnose_prometheus_anomaly。
trigger: user_initiated
severity: info
---

# 告警驱动故障闭环排查（Alert → Root Cause → Recovery）

## Goal

以 **Nightingale 告警** 为排查起点，走完"**告警拉取 → 多指标分析 → 根因输出 →
自愈建议**"完整闭环，输出一份可直接用于值班处置的结构化故障报告。本 skill 是
**告警类问题的唯一入口**；纯健康巡检（无告警语义）请用 `diagnose_prometheus_anomaly`。

## When to use

用户问法含"告警 / 为什么告警 / 有异常 / 出故障 / 挂了 / 排查一下 / 怎么处理"等
排障语义，且涉及具体服务或规则时，立即使用本 skill。典型问法：
- "nfc-saas-service 为什么告警了？"
- "看一下最近 5 分钟有什么告警，怎么处理"
- "服务请求耗时大于20s 这条告警怎么排查"
- "现在有哪些活跃告警，分别是什么原因"

**不要因为问法口语化（"咋回事 / 什么原因 / 怎么办"）而漏用本 skill。**

## 可用工具（全部只读，monitoring toolset）

| 阶段 | 工具 | 用法 |
|---|---|---|
| 1 拉告警 | `nightingale_active_alerts` | 活跃告警，参数 `rule_name`/`severity`/`limit` |
| 1 拉告警 | `nightingale_history_alerts` | 历史告警，参数 `minutes`/`rule_name`/`severity`/`limit` |
| 2 分析指标 | `prometheus_service_health` | 服务健康快照（五维一次拿全），参数 `service` 传服务名或 `nfc-.*` 正则 |
| 2 深挖指标 | `prometheus_metric_query` | PromQL 深度查询，参数 `query` |
| 辅助 | `prometheus_service_discovery` | 发现某前缀下实际服务名，参数 `service_prefix` |

告警事件字段：`rule_name`（规则名）、`severity`（S1-CRITICAL/S2-WARNING/S3-INFO）、
`service`（关联服务）、`group_name`（业务组）、`trigger_time`/`recover_time`、
`trigger_value`（触发值）、`is_recovered`（是否已恢复）。

## ⚠️ 关键纪律

1. **每个阶段至多 1-2 次工具调用**，能在同一轮并行的就并行发起，避免轮次耗尽。
2. **先拉告警再查指标**：告警是入口，从告警里拿 `service`/`rule_name` 后再分析指标，
   不要凭空猜服务名。
3. **只引用上表工具，不要发明任何其它工具名。** PromQL 只允许诊断文档中的指标名。
4. **发现 critical（S1）就直接汇总报告**，不要跑完所有检查。
5. **查询空结果 ≠ 正常**：标记 `no_data` 而非"正常"。

## 推荐流程（闭环，通常 3-5 次调用）

### 步骤 1：拉取告警（入口）

先判断用户要活跃告警还是历史告警，然后调用（可并行）：
- 活跃：`nightingale_active_alerts(limit=...)`
- 历史：`nightingale_history_alerts(minutes=..., limit=...)`（默认 10 分钟）

从返回 `events` 中提取每个告警的 `service`、`rule_name`、`severity`、`trigger_value`。
**如果没有告警，直接输出"当前无告警"并结束，不要继续查指标。**

### 步骤 2：对告警服务做多指标分析

对步骤 1 提取的每个 `service`（去重），调用一次：
`prometheus_service_health(service="<service>")`（五维快照一次拿全）。
若告警来自多个服务，用 `service="nfc-.*"` 一次覆盖，或并行按服务逐个调用。

### 步骤 3：深挖异常维度（只在快照发现异常时）

某维度异常（如错误率超标、DB 耗时过高、GC 频繁）时，用
`prometheus_metric_query(query="<PromQL>")` 深入定位到具体 endpoint / SQL / serverIp。
**不要对每个指标都深挖**，只针对异常项。

### 步骤 4：根因输出

结合「告警内容 + 健康快照 + 深挖结果」给出根因判断，格式见下。

### 步骤 5：自愈建议

给出**可执行的处置建议**，并按影响分三档：
- **紧急处置（critical）**：影响用户/资金，需立即止损（如摘流量、回滚、扩容）
- **修复建议（warning）**：需在窗口期内处理的配置/代码问题（如调阈值、加超时、扩内存）
- **观察项（info）**：需持续观察的潜在风险

**本 skill 只读，绝不执行任何变更操作**；建议是给人/后续自愈流程执行的。

## 指标阈值速查（来自诊断文档）

| 维度 | 指标 | 异常阈值 |
|---|---|---|
| 请求 | 错误请求占比 | >= 10% |
| 请求 | HTTP 200 占比 | <= 90% |
| 数据库 | DB 平均耗时 | >= 5s |
| SQL | SQL 错误占比 | >= 5% |
| JVM | Young GC 频率 | >= 5 次/分钟 |
| JVM | Old GC 频率 | >= 0.05 次/分钟 |
| 系统 | CPU 空闲 | <= 20% |
| 系统 | IO Wait | >= 1% |
| 系统 | 网络出入错误 | > 0 |

## Output format

输出结构化报告（JSON 或 Markdown 表格均可，务必完整覆盖闭环五要素）：

```json
{
  "alerts": [
    {
      "rule_name": "服务请求耗时大于20s",
      "severity": "S2-WARNING",
      "service": "nfc-saas-service",
      "trigger_time": "2026-08-31 10:00:00",
      "is_recovered": false
    }
  ],
  "metric_findings": [
    {
      "service": "nfc-saas-service",
      "dimension": "database",
      "indicator": "db_seconds",
      "current_value": 6.2,
      "threshold": 5.0,
      "severity": "critical"
    }
  ],
  "root_cause": "nfc-saas-service 依赖的数据库 X 平均耗时 6.2s 超过 5s 阈值，导致请求耗时上升触发告警",
  "recovery_suggestions": [
    {
      "priority": "warning",
      "action": "定位慢 SQL（见指标 detail），优化索引或拆分查询"
    }
  ]
}
```

## Edge cases

- **无告警**：直接结束，不要查指标。
- **告警但指标无数据（no_data）**：标记 `no_data`，说明可能是服务刚部署或采集延迟，不要误报"正常"。
- **多服务告警**：按服务分组分析，每个服务单独给根因。
- **同一规则多事件**：取最近一次/最严重一次作为代表，不要重复分析。
- **查询超时/失败**：连续 2 次失败跳过该检查并如实说明，不反复重试。

## What NOT to do

- **不要**跳过告警拉取直接查指标 —— 告警是定位服务异常的入口。
- **不要**对空结果下结论"正常"。
- **不要**为了完整性跑完所有检查 —— 发现 critical 即汇总。
- **不要**执行任何变更操作（本 skill 严格只读）。
- **不要**建议用户去其它页面看 —— 你的任务就是查询和分析完再给结论。
