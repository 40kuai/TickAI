# K8s + Harness + Skills — Design Spec

**Date:** 2026-06-17
**Status:** Approved (user "开始" 2026-06-17)
**Sprint:** 1 of 2 (Sub 3 = evolver manual-only this sprint)

## Goal

Extend OpsTicket with K8s cluster analysis using a Harness scheduling engine
and modular analysis "skills" that can be auto-optimized over time.

## Scope (this sprint)

- **Sub 1:** 6 K8s read-only tools (nodes, pods, events, deployments, services, contexts)
- **Sub 2:** Harness skeleton + scheduler + 1 thoroughly-written skill
- **Sub 3 (light):** Evolver interface + manual trigger only

## Hard constraints

| # | Constraint | Implementation |
|---|---|---|
| 1 | **Strict K8s permission limits** | Hardcoded command allowlist; reject any verb except `get` and `config get` |
| 2 | **Read-only** | No `delete`, `apply`, `patch`, `edit`, `exec`, `create`, `scale`, `rollout`, `cordon`, `drain`, `taint`, `label`, `annotate`, `cp`, `proxy`, `port-forward`, `debug`, `run` |
| 3 | **No environmental impact** | No kubeconfig write; no context switch; no `--save-config` |
| 4 | **Zero new deps for scheduler** | stdlib `threading.Timer` + custom registry |
| 5 | **One skill done thoroughly** | `detect_oom_killed.md` with explicit analysis steps + output format + decision matrix |
| 6 | **All existing tests still pass** | No regression in 208 existing tests |

## Architecture

```
opslib/
├── k8s/
│   ├── kubectl_runner.py    # subprocess wrapper, allowlist, timeout
│   ├── kubeconfig.py        # parse ~/.kube/config, list contexts
│   └── tools.py             # 6 LLM tools (4 get + 2 get contexts)
├── harness/
│   ├── scheduler.py         # stdlib threading.Timer registry
│   ├── runner.py            # run 1 skill: load .md → LLM → outcome
│   └── evolver.py           # LLM rewrites skill .md based on outcomes
├── models.py                # +SkillOutcome, +SkillVersion
└── ...

skills/
└── detect_oom_killed.md     # 1 skill, thoroughly written

pages/
└── 5_Skills.py              # Browse/Run/Outcomes/Scheduler tabs
```

## Data model

```python
class SkillOutcome(Base):
    id, skill_name, cluster_context, triggered_by
    run_id                       # FK to run_history (when applicable)
    findings_json                # JSON list of findings from LLM
    user_decision                # accepted | rejected | pending
    decision_at                  # datetime
    outcome_effect               # improved | no_change | worse | None
    measured_at, notes

class SkillVersion(Base):
    id, skill_name, version
    content                      # full .md content
    diff                         # diff vs previous
    reason                       # initial | manual | auto_evolve
    created_at
```

## K8s tool allowlist (hardcoded)

| Tool | kubectl command | Output |
|---|---|---|
| `check_k8s_nodes` | `kubectl get nodes -o json` | nodes + conditions + capacity |
| `check_k8s_pods` | `kubectl get pods -A -o json` | all pods across namespaces |
| `check_k8s_events` | `kubectl get events -A --sort-by=.lastTimestamp -o json` | warning events |
| `check_k8s_deployments` | `kubectl get deployments -A -o json` | deployments + replicas |
| `check_k8s_services` | `kubectl get services -A -o json` | services + cluster IPs |
| `list_k8s_contexts` | `kubectl config get-contexts -o json` | available contexts |

**Forbidden verbs** (rejected before subprocess even starts):
`delete, apply, patch, edit, exec, create, replace, scale, rollout, cordon,
drain, taint, label, annotate, set, cp, proxy, port-forward, attach, auth,
debug, run, logs`

## Skill file format

```markdown
---
name: detect_oom_killed
description: Find pods killed by OOMKiller in last 24h
trigger: scheduled_daily
severity: warning
---

# Detect OOMKilled Pods

[Markdown body — analysis instructions for the LLM]
```

## Test plan

64 new tests, 100% pass required. Existing 208 tests must remain green.

## Non-goals (this sprint)

- Auto-evolve skills (manual only)
- UI for editing skills (read-only view)
- Multi-cluster support
- Persistent scheduler (jobs don't survive restart)
- Skill result export
