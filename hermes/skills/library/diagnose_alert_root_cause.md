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

以 **Nightingale 告警** 为排查起点，走完"**告警拉取 → 多指标分析 → 发布变更关联 → 根因输出 →
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
| 3 发布关联 | `jenkins_service_jobs` | 按服务名定位对应 Jenkins job，参数 `service` |
| 3 发布关联 | `jenkins_build_records` | 查 job 最近发布记录（时间/结果/commit SHA），参数 `job_name`/`limit` |
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

## ⏱️ 时间窗口对齐（重要，先读）

**指标查询必须对齐告警时间，不能默认查最近 10 分钟。** 告警 `trigger_time` 可能已是
几十分钟甚至几小时前，若按工具默认窗口（最近 10 分钟）查指标，会查不到告警发生时的
状态，误判"现在正常"（假阴性）。

规则：

1. **取告警时间窗**：对每条告警，用 `trigger_time` 计算查询窗口：
   `start = trigger_time - 5分钟`，`end = trigger_time + 5分钟`（Unix 时间戳）。
   已恢复告警用 `recover_time` 作为 `end` 上限，但不得早于 `trigger_time`。
2. **显式传参**：`prometheus_service_health` 和 `prometheus_metric_query`
   都支持 `start`/`end` 参数，**必须显式传入**对齐后的窗口，不要用默认值。
3. **活跃告警（未恢复）**：`trigger_time` 之后仍在告警，指标窗口取
   `[trigger_time, 当前时间]`，覆盖整个告警持续期。
4. **多条告警时间相近**：归并为同一窗口，避免重复查询；窗口跨度不超过 1 小时。

**举例**：告警 `trigger_time=1788159715`（即 10:00）→ 查询应传
`start=1788159415, end=1788160015`，而非默认的最近 10 分钟。

## 推荐流程（闭环，通常 4-7 次调用）

### 步骤 1：拉取告警（入口）

先判断用户要活跃告警还是历史告警，然后调用（可并行）：
- 活跃：`nightingale_active_alerts(limit=...)`
- 历史：`nightingale_history_alerts(minutes=..., limit=...)`（默认 10 分钟）

从返回 `events` 中提取每个告警的 `service`、`rule_name`、`severity`、`trigger_value`。
**如果没有告警，直接输出"当前无告警"并结束，不要继续查指标。**

### 步骤 2：对告警服务做多指标分析

对步骤 1 提取的每个 `service`（去重），调用一次：
`prometheus_service_health(service="<service>", start=<对齐后start>, end=<对齐后end>)`
（五维快照一次拿全，**窗口须按上方「时间窗口对齐」取告警时间，不要用默认最近 10 分钟**）。
若告警来自多个服务，用 `service="nfc-.*"` 一次覆盖，或并行按服务逐个调用。

### 步骤 3：深挖异常维度（只在快照发现异常时）

某维度异常（如错误率超标、DB 耗时过高、GC 频繁）时，用
`prometheus_metric_query(query="<PromQL>", start=<对齐后start>, end=<对齐后end>)`
深入定位到具体 endpoint / SQL / serverIp（同样传对齐后的窗口）。
**不要对每个指标都深挖**，只针对异常项。

### 步骤 4：发布变更关联（判断是否由发布引起）

**发布变更是故障最常见的根因之一，必须做变更关联。** 对每个告警服务：

1. 用 `jenkins_service_jobs(service="<服务名>")` 定位该服务对应的 Jenkins job
   （服务名如 `nfc-saas-service` 通常映射到 `<服务名>-k8s-<环境>` 类 job）。
2. 用 `jenkins_build_records(job_name="<job名>", limit=10)` 取最近发布记录，
   比对告警 `trigger_time`：**告警时间窗内（前后约 1 小时内）是否有 SUCCESS/FAILURE 发布**。
3. 若命中发布 → 根因优先判为「发布变更引入」（如新代码异常/配置变更/依赖升级），
   并在自愈建议中给出**回滚该次发布**作为紧急处置选项。

**发布关联只查与告警服务同名的 job，不要全量拉取**；若工具查询失败或无可疑发布，
如实说明"未发现相关发布"，不要臆造。

### 步骤 5：根因输出

结合「告警内容 + 健康快照 + 深挖结果 + 发布关联」给出根因判断，格式见下。

### 步骤 6：自愈建议

给出**可执行的处置建议**，并按影响分三档：
- **紧急处置（critical）**：影响用户/资金，需立即止损（如摘流量、回滚、扩容）
- **修复建议（warning）**：需在窗口期内处理的配置/代码问题（如调阈值、加超时、扩内存）
- **观察项（info）**：需持续观察的潜在风险

**本 skill 只读，绝不执行任何变更操作**；建议是给人/后续自愈流程执行的。

#### 建议可执行性校验（每条建议三要素）

每条 `recovery_suggestions` 必须尽量同时具备三要素，缺一不可被当作"已可执行"：

| 要素 | 要求 | ✅ 好例 | ❌ 坏例 |
|---|---|---|---|
| **对象（What）** | 指明动作对象（服务/job/实例） | 回滚 `nfc-saas-service-k8s-prod` | 回滚服务 |
| **定位（Where）** | 带可定位信息（endpoint/destId/commit） | 慢 SQL：`endpoint=queryUserList`、`destId=rm-xxx` | 优化慢 SQL |
| **动作（How）** | 具体操作 + 优先级 | kill 阻塞事务；扩容 2 副本；调阈值 20s→30s | 关注一下 |

**无法满足三要素的建议 → 标注 `needs_human: true`**，如实说明"需人工补全"，**不得假装可执行**。
自愈建议是 KR2 自愈流程的输入，三要素不全的建议会让执行端无法落地。

## 告警收敛（避免报告割裂）

同一时刻往往有多条告警，**先收敛再分析**，防止输出割裂、重复的结论。三层收敛：

1. **同规则多事件** → 取最近/最严重一条作为代表，不重复分析。
2. **同服务多规则** → 看这些规则是否指向同一异常维度（如都指向 DB 慢：请求耗时 +
   200占比低 + SQL 慢）→ 若是，收敛为**单一根因**，其余规则作为佐证一并列出；
   若指向不同维度，则按维度分开给根因。
3. **跨服务共享依赖** → 多个服务同时告警时，从指标 `destId`/`endpoint` 判断是否
   依赖同一 MySQL/Redis：若是 → 收敛为「共享依赖故障」单一根因，服务降为"受影响面"；
   若否 → 按服务保留。

**不确定是否同源时，宁可保留分服务报告并标注"疑似同源"，不要强行收敛丢失信息。**

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

输出结构化报告（JSON 或 Markdown 表格均可，务必完整覆盖闭环要素）：
- `alerts`（告警）→ `metric_findings`（指标分析）→ `deployment`（发布关联）→
  `root_cause`（根因）→ `recovery_suggestions`（自愈建议）

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
  "deployment": {
    "related": true,
    "job_name": "nfc-saas-service-k8s-prod",
    "build": 1024,
    "build_time": "2026-08-31 09:30:00",
    "result": "SUCCESS",
    "commit_sha": "5928dd8",
    "matched": "告警触发前 30 分钟有一次成功发布"
  },
  "root_cause": "nfc-saas-service 于 09:30 发布（#1024）后数据库平均耗时上升至 6.2s 触发告警，疑似新版本引入慢 SQL",
  "recovery_suggestions": [
    {
      "priority": "critical",
      "action": "回滚 nfc-saas-service-k8s-prod 至上一稳定版本"
    },
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
