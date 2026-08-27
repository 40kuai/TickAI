---
name: detect_oom_killed
description: Find pods killed by the Linux OOMKiller in the last 24 hours, identify memory-limit misconfigurations, and recommend fixes.
trigger: scheduled_daily
severity: warning
---

# Detect OOMKilled Pods

## Goal

Find every pod that was OOMKilled in the last 24 hours, identify the root
cause (missing limit, undersized limit, or memory leak), and recommend a
specific fix for each case.

## When to use

- User asks "any pods crashing from memory?"
- User asks "what's eating memory on this cluster?"
- Proactive daily check for production clusters
- After a deploy that bumped memory usage

## What to check

### Step 1: Get recent warning events

Call `check_k8s_events(context=...)` (default events are Warning only,
sorted by most recent). Look for events with `reason: "OOMKilling"` in the
`message` field. Note the timestamp and the involved pod.

For each OOMKilling event, extract:
- namespace
- pod name
- container name
- the node it happened on (in event.involvedObject for pods, check pod spec)

### Step 2: Get the current state of those pods

For each pod found in Step 1, call `check_k8s_pods(context=..., namespace=...)`
filtered to the affected namespace. For each candidate pod, find:
- `spec.containers[*].resources.limits.memory` — is there a memory limit?
- `status.containerStatuses[*].restartCount` — how many times has it restarted?
- `status.containerStatuses[*].lastState.terminated.reason` — was it "OOMKilled"?

### Step 3: Look at the node's memory pressure

Call `check_k8s_nodes(context=...)` and check `status.conditions`. If any
node shows `MemoryPressure: True`, that's a system-level OOM risk, not just
a pod-level one.

### Step 4: Classify each finding

For each OOMKilled pod, assign one of these categories:

| Category | Signal | Action |
|---|---|---|
| **A. No memory limit** | `resources.limits.memory` is absent | Add a memory limit. Recommend 1.5x observed peak usage. |
| **B. Limit too low** | `resources.limits.memory` set, but used more | Raise the limit. Recommend current limit × 1.5 (rounded up). |
| **C. Memory leak** | `restartCount` keeps growing over days | Investigate heap. Recommend adding `kubectl logs --previous` analysis. |
| **D. Node pressure** | Node has `MemoryPressure: True` | Reduce node-level pod density. Add node, or evict low-priority pods. |
| **E. JVM/GC issue** | Java/Python with low CPU but high mem, slow OOM | Heap tuning. Recommend enabling heap metrics. |

## Output format

Return a JSON array. Each item:

```json
{
  "namespace": "default",
  "pod": "api-server-abc123",
  "container": "api",
  "oom_count_last_24h": 3,
  "memory_limit": "512Mi" | null,
  "category": "A" | "B" | "C" | "D" | "E",
  "recommendation": "Add memory limit. Recommended: 768Mi based on observed peak.",
  "evidence": [
    "Event at 2025-06-16T10:23:01Z: OOMKilling container api",
    "Pod spec has no resources.limits.memory"
  ]
}
```

If no OOMKills found in the last 24h, return an empty array `[]`.

## What NOT to recommend

- **NEVER** restart the pod as a fix — k8s already does that, and it just delays the next OOM
- **NEVER** delete the pod as a fix — same reason
- **NEVER** recommend removing the memory limit as a "fix" — that just moves the OOM to the node
- **AVOID** vague advice like "monitor memory" — give a specific number
- **AVOID** recommending config changes that require kubectl apply — this skill is read-only

## Edge cases

- **Pod deleted since OOM**: include in the report with `note: "pod no longer exists"`. Useful for postmortems.
- **System pods (kube-system)**: still report, but mark as `system: true` in the output.
- **Multiple containers in one pod, only one OOM**: report only the affected container.
- **OOM during init container**: include `container_type: "init"` in the output.

## What to do with the output

The findings go into the `SkillOutcome` table. The user reviews them in the
OpsTicket UI and marks each as accepted / rejected. After the user acts
on the recommendation, they (optionally) record the actual outcome:
improved / no_change / worse.

The evolver reads these outcomes to refine this skill over time.
