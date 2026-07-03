# OpsTicket MVP — Design Spec

**Date:** 2026-06-16
**Scope:** Single-user internal ops tool, Stage 1
**Status:** Approved, implementing

## Goal

Free the operator from daily SSH-and-paste routines by exposing them through a web UI and an LLM chat.

## Locked Decisions

| Question | Answer |
|---|---|
| First sub-project | Remote command execution (SSH to server, run command, persist result) |
| Frontend stack | Streamlit (already in venv) |
| Auth model | Single user, no login page (network isolation) |
| Credential storage | Plaintext in SQLite, chmod 600, .gitignored, future field for encryption |
| LLM integration | Yes — adds "Ask LLM" page + 2 read-only tools (query_runs, list_servers) |
| Storage | SQLite, single file |
| Deployment | Local `streamlit run`, no Docker for MVP |

## Architecture

```
Streamlit (single process)
├── pages/ (4 user-facing pages + Home)
└── opslib/ (pure Python, no Streamlit imports)
    ├── db.py, models.py     # SQLAlchemy
    ├── ssh_runner.py        # SSH execution + auto-audit
    ├── audit.py             # read run_history
    ├── tools.py             # 2 new LLM tools
    └── llm_agent.py         # wraps caller.TokenHubClient + persists

Reuses: tools/ (registry, disk), caller.py (TokenHubClient)
DB: SQLite at data/opsticket.db
```

## Data Model (3 tables)

- `servers` — id, name (unique), host, port, username, password, tags, notes, is_active, created_at, updated_at, last_seen_at
- `run_history` — id, server_id, command, status, exit_code, started_at, finished_at, duration_ms, stdout, stderr, structured_result, triggered_by, triggered_context
- `llm_conversations` — id, title, created_at, updated_at, messages_json, total_runs

## Pages

1. **Home** — status dashboard (counts, recent runs, LLM status)
2. **Servers** — CRUD with test-connection button
3. **Check Disk** — pick server, run df -Th, show parsed + raw result
4. **History** — filterable audit log with expandable details
5. **Ask LLM** — chat with LLM that can call check_disk_usage / query_runs / list_servers

## LLM Tools Available

- `check_disk_usage` (reused from `tools/disk.py`) — SSH to server, run df -Th
- `query_runs` (new) — query run_history with filters
- `list_servers` (new) — list configured servers (password redacted)

LLM has **read-only** access. Server create/edit/delete only via UI.

## Security Guardrails

- DB file: `chmod 600` on creation
- `.env` and `data/` in `.gitignore`
- `password` field kept as-is (cleartext) but documented for future encryption
- LLM never receives passwords in `list_servers` output (redacted)

## Testing

TDD for opslib (db, ssh_runner, audit, tools, llm_agent). Streamlit pages verified by manual smoke test.

## Out of Scope (Stage 1)

- Multi-user / RBAC
- Approval workflows
- Notifications (email/IM)
- Custom commands beyond `df -Th`
- Real-time monitoring
- Schema migration tooling (Alembic)
- Auto-encryption of credentials
