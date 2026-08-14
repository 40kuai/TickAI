# Prometheus 业务异常诊断工具与技能设计

## 概述

通过 ARMS Prometheus 监控指标，快速定位业务异常。提供 LLM 可调用的查询工具和结构化的异常诊断技能。

## 架构

```
用户请求 → LLM → 技能(diagnose_prometheus_anomaly) → 工具(prometheus_instant/range_query) → ARMS API
```

## 工具

| 工具 | 职责 | 参数 |
|------|------|------|
| `prometheus_instant_query` | 即时查询，返回单时间点指标值 | query, time(可选) |
| `prometheus_range_query` | 范围查询，返回时间序列 | query, start, end, step |

工具只做原始 PromQL 查询，不涉及阈值判断。

## 技能

`diagnose_prometheus_anomaly` — 按类别分组的检查项：

| 类别 | 检查项 | 阈值 |
|------|--------|------|
| 服务请求 | 错误百分比、HTTP 200 占比 | >=10%, <=90% |
| 数据库 | 请求耗时、错误百分比、SQL 错误 | >=5s, >=5%, >=5% |
| JVM | Young GC 频率/耗时、Old GC 频率/耗时 | >5次/分, >8s, >0.05次/分, >3s |
| 系统 | CPU 空闲、IO Wait、网络错误 | <=20%, >=1%, >0 |

## 配置

- `PROMETHEUS_URL` — ARMS Prometheus API 地址
- `PROMETHEUS_TOKEN` — Bearer Token