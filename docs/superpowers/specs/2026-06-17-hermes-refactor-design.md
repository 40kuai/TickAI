# Hermes Refactor — Project-wide Reorganization

**Date:** 2026-06-17
**Status:** Draft (user approved approach, drafting spec)
**Scope:** Whole-project refactor. No new features, no API-breaking changes for end users.

## Goal

Reorganize OpsTicket as a "Hermes" architecture — a single-process, multi-agent,
iterable AI agent. Clean module boundaries, deleted redundancy, single source of
truth for tests and tools.

## Non-Goal

- No new features
- No agent runtime improvements (e.g., better iteration loop, new metrics)
- No external API changes
- No CI/Docker/deployment changes (out of scope, can be follow-up)

## Hermes Architecture (in plain words)

A "Hermes" agent has 5 layers:

| Layer | Job | Where it lives |
|---|---|---|
| **Core** | The LLM loop (think/act/observe) + LLM client | `hermes/core/` |
| **Agents** | Concrete agent personalities (chat, skill-runner, evolver) | `hermes/agents/` |
| **Skills** | Modular "what the agent knows" (markdown + lifecycle) | `hermes/skills/` |
| **Tools** | External capabilities the agent can call (K8s, SSH) | `hermes/tools/` |
| **Data** | Persistent state (DB, models, conversation history) | `hermes/data/` |

Multiple agents share the same Core, Tools, and Data. They differ in their system
prompt and which Skills they execute.

## New Directory Layout

```
/Users/40kuai/Documents/ai/
├── hermes/                          # THE framework
│   ├── __init__.py
│   ├── core/                        # LLM client + prompt builders
│   │   ├── __init__.py
│   │   ├── llm.py                   # TokenHubClient (renamed from caller.py)
│   │   └── prompts.py               # System-prompt builders + LANGUAGE_DIRECTIVES
│   ├── agents/                      # Concrete agent executors
│   │   ├── __init__.py
│   │   ├── chat.py                  # Conversational agent (Ask LLM page)
│   │   ├── skill_runner.py          # SkillRunner (moved from harness/runner.py)
│   │   └── skill_evolver.py         # SkillEvolver (moved from harness/evolver.py)
│   ├── skills/                      # Skill content + scheduling
│   │   ├── __init__.py
│   │   ├── loader.py                # load_skill, list_skills, save_skill
│   │   ├── scheduler.py             # Harness (periodic jobs)
│   │   └── library/                 # The actual .md skills (git-tracked)
│   │       ├── __init__.py
│   │       └── detect_oom_killed.md
│   ├── tools/                       # Tool registry + tool families
│   │   ├── __init__.py
│   │   ├── registry.py              # (moved from tools/registry.py)
│   │   ├── ssh/                     # SSH tools (merged from tools/disk.py + tools/system.py)
│   │   │   ├── __init__.py
│   │   │   ├── runner.py
│   │   │   ├── disk.py
│   │   │   ├── resources.py
│   │   │   └── services.py
│   │   └── k8s/                     # K8s tools
│   │       ├── __init__.py
│   │       ├── kubectl_runner.py
│   │       ├── kubeconfig.py
│   │       └── tools.py             # 6 LLM tools
│   ├── data/                        # Persistence layer (SQLAlchemy)
│   │   ├── __init__.py
│   │   ├── db.py                    # engine, session_scope, init_db
│   │   └── models.py                # All SQLAlchemy models
│   ├── i18n/
│   │   ├── __init__.py
│   │   └── strings.py               # Translations
│   └── config/
│       ├── __init__.py
│       └── settings.py              # Env-driven config
│
├── ui/                              # Streamlit UI
│   ├── __init__.py
│   ├── app.py                       # Main entry
│   ├── i18n_helpers.py              # render_language_selector
│   └── pages/
│       ├── 1_Servers.py
│       ├── 2_Check_Disk.py
│       ├── 3_History.py
│       ├── 4_Ask_LLM.py
│       └── 6_Skills.py
│
├── tests/                           # ALL tests here (merged)
│   ├── core/
│   ├── skills/
│   ├── tools/
│   │   ├── ssh/
│   │   └── k8s/
│   └── agents/
│
├── docs/
│   ├── README.md
│   └── superpowers/specs/
│
├── data/                            # Runtime DB (gitignored)
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── main.py                          # Entry: streamlit run ui/app.py
```

## Files Deleted (Redundancy)

| File | Reason |
|---|---|
| `_check_lang.py` | Debug scratch left from earlier session |
| `tools/dice.py` | Demo tool, not production |
| `tests/test_dice.py` | Test for deleted demo |
| `opsticket/opslib/tools.py` | Duplicate of `tools/disk.py` (same SSH tool) |
| `opsticket/opslib/system_tools.py` | Duplicate of `tools/system.py` (system info) |
| `opsticket/opslib/run_check.py` | Wrapper that re-exports `check_disk_on_server` |
| `opsticket/opslib/audit.py` | Empty/legacy audit helpers (audit lives in ssh_runner) |
| `opsticket/opslib/ssh_runner.py` | Merged into `hermes/tools/ssh/runner.py` |
| `opsticket/opslib/llm_agent.py` | Replaced by `hermes/core/agent.py` |
| `opsticket/opslib/harness/*.py` | Moved to `hermes/skills/` + `hermes/agents/` |
| `opsticket/opslib/k8s/*.py` | Moved to `hermes/tools/k8s/` |
| `opsticket/opslib/i18n.py` | Moved to `hermes/i18n/strings.py` |
| `opsticket/opslib/config.py` | Moved to `hermes/config/settings.py` |
| `opsticket/opslib/db.py` | Moved to `hermes/data/db.py` |
| `opsticket/opslib/models.py` | Moved to `hermes/data/models.py` |
| `opsticket/opslib/__init__.py` | Empty — replaced by hermes/ |
| `opsticket/opslib/k8s/__init__.py` | Empty |
| `opsticket/opslib/harness/__init__.py` | Empty |
| `opsticket/tests/` (16 files) | Merged into top-level `tests/` |
| `tests/` (root, 5 files) | Already mostly migrated; leftover `test_dice.py` deleted |
| `tools/` (root) | Moved to `hermes/tools/ssh/` + `hermes/tools/k8s/` |
| `tools/__init__.py` | Empty |
| `caller.py` (root) | Moved to `hermes/core/llm.py` |

**Net**: 25+ files deleted, ~10 files renamed/moved, structure is now strictly
layered.

## Module Boundaries

### hermes/core

LLM client and prompt builders. No business logic, no DB access.
Depends on: nothing (pure utilities).

```
core/llm.py      — TokenHubClient (HTTP client, no agent logic)
core/prompts.py  — System-prompt builders, LANGUAGE_DIRECTIVES
```

### hermes/agents

Concrete agent personalities. Each one is a HermesAgent subclass with:
- Specific system prompt
- Specific tool subset
- Specific behavior

```
agents/chat.py           — Conversational agent for the Ask LLM page
agents/skill_runner.py   — SkillRunner (renamed from harness/runner.py)
agents/skill_evolver.py  — SkillEvolver (renamed from harness/evolver.py)
```

### hermes/skills

Skill content + scheduling. No agent logic (executor lives in `hermes/agents/`).

```
skills/loader.py    — load_skill, list_skills, save_skill, _parse_skill
skills/scheduler.py — Harness (periodic job runner)
skills/library/     — The .md files (git-tracked, edited by humans)
```

### hermes/tools

External capabilities. Each tool family is its own subpackage.

```
tools/registry.py       — ToolRegistry (singleton)
tools/ssh/              — SSH tools (merged from tools/disk.py + tools/system.py)
  runner.py             — ssh.run(host, user, pwd, cmd)
  disk.py               — check_disk_usage
  resources.py          — check_resources
  services.py           — list_services
tools/k8s/              — K8s tools (moved from opsticket/opslib/k8s/)
  kubectl_runner.py
  kubeconfig.py
  tools.py              — 6 LLM tools (check_k8s_* + list_k8s_contexts)
```

### hermes/data

Persistence. Owns the DB schema. Direct SQLAlchemy access from agents/UI.

```
data/db.py      — engine, session_scope, init_db
data/models.py  — All SQLAlchemy models
```

### hermes/i18n

```
i18n/strings.py    — TRANSLATIONS dict, t(), render_language_selector()
```

### hermes/config

```
config/settings.py — LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, OPS_DB_PATH, etc.
```

### ui

Streamlit pages only. Imports from hermes.*, never the other way.

```
ui/app.py            — Main entry (page config, sidebar, navigation)
ui/pages/1_Servers.py
ui/pages/2_Check_Disk.py
ui/pages/3_History.py
ui/pages/4_Ask_LLM.py
ui/pages/6_Skills.py
ui/i18n_helpers.py   — render_language_selector (re-export from hermes.i18n)
```

## Data Flow (one example: user runs a skill)

```
[User clicks "Run skill" in 6_Skills.py]
            │
            ▼
[ui/pages/6_Skills.py]  reads language from session_state, kubeconfig from session_state
            │
            ▼
[hermes.agents.skill_runner.SkillRunner.run(skill_name, language, context)]
            │  imports:
            ▼
[hermes.skills.loader.load_skill]  → reads skills/library/detect_oom_killed.md
            │
            ▼
[hermes.core.agent.HermesAgent]   → builds system prompt (uses LANGUAGE_DIRECTIVES)
            │
            ▼
[hermes.core.llm.TokenHubClient.chat(messages, tools)]
            │  tools are from:
            ▼
[hermes.tools.registry.list_schemas_by_toolset("k8s")]
            │  tool handlers use:
            ▼
[hermes.tools.k8s.tools] → [hermes.tools.k8s.kubectl_runner] → kubectl subprocess
            │
            ▼
[hermes.data.store.OutcomeStore.create()]  → save SkillOutcome
            │
            ▼
[ui/pages/6_Skills.py]  → display findings
```

## Test Strategy

- All tests under `tests/`, no split between `tests/` (root) and `opsticket/tests/`
- Mirrors source structure: `tests/core/`, `tests/skills/`, `tests/tools/k8s/`, etc.
- Run with: `python -m unittest discover -s tests`
- **Must remain 312+ tests passing** throughout the refactor (refactor only, no feature changes)

## Migration Plan (high-level)

This is a multi-step migration. Order matters — each step keeps the build green:

1. **Create new structure** — mkdir `hermes/`, `ui/`, new `tests/` subdirs
2. **Move data layer** — `hermes/data/{db,models}.py` (low-risk, no dependencies)
3. **Move i18n + config** — `hermes/i18n/`, `hermes/config/`
4. **Move tools** — `hermes/tools/{registry.py, ssh/, k8s/}`
5. **Move core** — `hermes/core/{agent.py, llm.py, prompts.py}`
6. **Move skills** — `hermes/skills/{loader, runner, evolver, scheduler}.py` + `library/`
7. **Move agents** — `hermes/agents/{chat, skill_runner, skill_evolver}.py`
8. **Move UI** — `ui/app.py`, `ui/pages/`, `ui/i18n_helpers.py`
9. **Consolidate tests** — merge `tests/` (root) + `opsticket/tests/` → `tests/`
10. **Delete old** — remove `opsticket/opslib/`, `opsticket/tests/`, `tools/`, `tests/dice.py`, `_check_lang.py`, `caller.py`
11. **Update entry point** — `main.py` runs `streamlit run ui/app.py`
12. **Verify** — all 312 tests still pass, browser smoke test

Each step verified with: `python -m unittest discover -s tests` (must stay 0 failures)

## Backward Compatibility

- **Public behavior**: identical (same Streamlit pages, same tools, same DB schema)
- **Internal imports**: will change — anyone importing from `opsticket.opslib.*` or
  `tools.*` or `caller` will need to update
- **No users outside this project** — safe to break internal imports

## What stays the same

- All 312 tests' assertions (only file paths change, not test logic)
- Streamlit page URLs (`/`, `/Servers`, `/Check_Disk`, `/History`, `/Ask_LLM`, `/Skills`)
- `.env` keys (no rename)
- DB schema (no model changes)
- K8s tool allowlist (security boundary stays)

## Out of Scope (this refactor)

- Multi-cluster support
- WebSocket-based UI updates
- Persistent scheduler (jobs don't survive restart)
- Skill marketplace (sharing skills across users)
- Auto-evolve on schedule
- Docker / CI / deployment
- Multi-language skill bodies (only `LANGUAGE_DIRECTIVES` is translated)
