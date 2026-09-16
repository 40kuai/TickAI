---
name: detect_oom_killed
description: >-
  分析 k8s 集群中因 OOMKilled 被杀死的 Pod（仅限 k8s/Pod 场景），定位根因
  （缺内存 limit、limit 过小、内存泄漏、节点内存压力）并给出修复建议。
trigger: user_initiated
severity: warning
---

# 检测 OOMKilled Pod（OOM 排查）

## Goal

找出最近时间范围内被 **OOMKilled** 的 Pod，定位每个 Pod 的根因
（**缺少内存 limit / limit 过小 / 内存泄漏 / 节点内存压力**），
并为每个情况给出**具体、可执行**的修复建议。本 skill 严格只读，
**仅限 k8s 集群 / Pod 场景使用，全程不会对集群产生任何影响。**

## When to use

**仅在用户提及 k8s 分析场景时使用**——即用户问法涉及 k8s 集群、Pod、
namespace、Deployment 等 k8s 语境，且语义与内存被杀 / OOM 相关：

- "k8s 里哪些 Pod 因为内存被杀 / OOM / OOMKilled？"
- "集群里有什么 Pod 在崩溃 / 反复重启？"
- "这个 Pod 为什么被杀了 / 为什么 OutOfMemory？"
- "k8s 集群里谁在吃内存？"
- 用户明确要求检查 k8s Pod 的 OOMKilled 状态或内存 limit 配置

**不要因为问法口语化（"咋回事 / 怎么一直重启"）而漏用本 skill**，前提仍是 k8s 语境。

**排除场景（不使用本 skill）**：非 k8s 场景——如询问某台物理机/服务器内存占用、
普通 Linux 进程 OOM、非 k8s 平台的容器内存问题等，应使用其它工具而非本 skill。

## 可用工具（全部只读，k8s toolset）

| 阶段 | 工具 | 用法 |
|---|---|---|
| 0 检查环境 | `list_k8s_contexts` | 确认 kubeconfig 配置正常、存在可用 context |
| 1 拉事件 | `check_k8s_events` | 按最近时间返回 Warning 事件，找 `reason: "OOMKilling"` |
| 2 查 Pod | `check_k8s_pods` | 查 Pod 的 `limits.memory`、`restartCount`、`lastState.terminated.reason` |
| 3 查节点 | `check_k8s_nodes` | 查节点 `conditions` 中的 `MemoryPressure`（系统级 OOM 风险） |

## 可调参数（通过 cluster_context 传入）

`run_skill` 的 `cluster_context` 里可携带以下参数（**全部可选**，缺省用默认值）：

- `namespace=<命名空间>`：只查指定命名空间；缺省全集群（`-A`）。
- `hours=<小时数>`：时间范围，缺省 24（接受 1 / 6 / 12 / 24 等）。
- `context=<kube上下文>`：指定 kubeconfig 上下文；缺省当前上下文。

用户没有指定时，一律使用默认值，不要凭空猜测。

## 推荐流程
（通常 3-4 次调用）

0. **前置检查 kubeconfig**：先调用 `list_k8s_contexts` 确认 kubeconfig 配置正常、
   存在可用 context。若用户指定了 `context=<ctx>`，确认该 context 存在；不存在则
   提示用户并回退到当前上下文。若 kubeconfig 异常 / 无任何可用 context，直接返回
   "kubeconfig 不可用，无法执行 OOM 分析"，**不要继续后续查询**。
1. **拉事件**：调用 `check_k8s_events(context=<ctx>)`，从结果中筛选
   `reason` 为 `OOMKilling` 的事件。提取：namespace、Pod 名、容器名、时间戳。
   若按时间范围筛选，只保留最近 `hours` 小时内的事件。
2. **查 Pod 详情**：对受影响的命名空间调用
   `check_k8s_pods(context=<ctx>, namespace=<ns>)`，对每个候选 Pod 检查：
   - `spec.containers[*].resources.limits.memory` —— 是否配置了内存 limit？
   - `status.containerStatuses[*].restartCount` —— 重启了多少次？
   - `status.containerStatuses[*].lastState.terminated.reason` —— 是否为 `OOMKilled`？
3. **查节点压力**：调用 `check_k8s_nodes(context=<ctx>)`，检查
   `status.conditions` 中是否有节点 `MemoryPressure: True`（系统级 OOM 风险）。
4. **分类并汇总**：按「分类与建议」表对每个发现分类，输出中文结构化报告。

**纪律**：能在同一轮并行的工具调用就并行发起；找到明确根因（尤其 critical）
就立即汇总报告，不要跑完所有检查；不要对同一数据重复查询。

## 分类与建议

| 分类 | 信号 | 建议 |
|---|---|---|
| **A. 缺少内存 limit** | `limits.memory` 未配置 | 添加内存 limit，建议为观测峰值的 1.5 倍 |
| **B. limit 过小** | 已配置 limit 但实际用量超过 | 调高 limit，建议当前 limit × 1.5（向上取整） |
| **C. 内存泄漏** | `restartCount` 多天持续增长 | 排查堆/内存占用，建议用 `kubectl logs --previous` 分析 |
| **D. 节点压力** | 节点 `MemoryPressure: True` | 降低节点 Pod 密度：扩容节点，或驱逐低优先级 Pod |
| **E. JVM/GC 问题** | Java/Python 低 CPU 高内存、缓慢 OOM | 堆调优，建议开启堆内存指标 |

## 输出格式

返回一个 JSON 数组，每个被 OOMKilled 的 Pod 一项。**文本字段（recommendation /
evidence / note）用中文输出**，技术字段（namespace / pod / memory_limit /
category / 时间戳）保持原样：

```json
[
  {
    "namespace": "default",
    "pod": "api-server-abc123",
    "container": "api",
    "oom_count": 3,
    "memory_limit": "512Mi",
    "category": "B",
    "recommendation": "调高内存 limit 到 768Mi（当前 512Mi × 1.5）",
    "evidence": [
      "事件 2025-06-16T10:23:01Z: OOMKilling 容器 api",
      "Pod 已配置 limits.memory=512Mi 但实际用量超过该值"
    ]
  }
]
```

若指定时间范围内没有 OOMKilled，返回空数组 `[]`。

## 边界情况

- **Pod 已删除**：仍在报告中保留，加 `note: "pod 已不存在"`（可用于事后复盘）。
- **kube-system 系统 Pod**：仍然报告，但在输出中标记 `system: true`。
- **多容器 Pod 仅一个容器 OOM**：只报告受影响的容器。
- **init 容器 OOM**：在输出中加 `container_type: "init"`。
- **事件为空 / 工具超时**：如实说明"无数据/查询失败"，不要对空结果下结论。

## 不要做什么

本 skill **只读纪律**：分析过程中只调用 `kubectl get`（事件 / Pod / 节点）类只读查询，
**绝不**执行 `create` / `delete` / `apply` / `scale` / `exec` / `edit` 等任何会改变集群状态的操作，
全程不会对集群产生任何影响。

- **不要**把重启 Pod 当修复方案——k8s 会自动重启，那只是推迟下一次 OOM。
- **不要**把删除 Pod 当修复方案——同理。
- **不要**建议删除内存 limit 作为"修复"——那只是把 OOM 移到节点上。
- **不要**给模糊建议（如"监控内存"）——必须给出具体数字。
- **不要**建议需要 `kubectl apply` 的变更——本 skill 严格只读，建议是给人执行的。
