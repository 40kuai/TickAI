"""Hermes — OpsTicket core package.

Top-level exports for UI code (backward compatible with opsticket.opslib):
- `db` — the db *module* (has `init_db`, `session_scope`)
- `config` — settings module (has `LLM_API_KEY`, etc.)
- `audit` — compatibility object (`audit.get_servers()`, `audit.list_runs()`)
- Models are exported directly (`from hermes import Server, RunRecord`)
"""
# Make submodules directly importable
from hermes.config import settings as config  # noqa: F401
from hermes.data import db  # noqa: F401  (db module itself)
# Also export db's functions for convenience
from hermes.data.db import init_db, session_scope  # noqa: F401
from hermes.data.models import (  # noqa: F401
    Server,
    RunRecord,
    Conversation,
    SkillOutcome,
    SkillVersion,
)


# -------------------------------------------------------------------
# Legacy 'audit' module compatibility (was removed in refactor)
# Replaces: from opsticket.opslib import audit
# Usage:  from hermes import audit
#         audit.get_servers()  →  List[Server]
#         audit.list_runs(100) → List[RunRecord]
# -------------------------------------------------------------------
def get_servers():
    """List all servers. Replacement for audit.get_servers()."""
    with session_scope() as s:
        return list(s.query(Server).all())


def list_runs(
    limit: int = 100,
    server_name: str = None,
    status: str = None,
    triggered_by: str = None,
    since=None,
):
    """List recent runs, newest first. Replacement for audit.list_runs()."""
    from sqlalchemy.orm import joinedload

    with session_scope() as s:
        q = s.query(RunRecord).options(joinedload(RunRecord.server))
        if server_name:
            q = q.join(Server).filter(Server.name == server_name)
        if status:
            q = q.filter(RunRecord.status == status)
        if triggered_by:
            q = q.filter(RunRecord.triggered_by == triggered_by)
        if since:
            from datetime import datetime, timezone
            q = q.filter(RunRecord.started_at >= datetime.now(timezone.utc) - since)
        return list(q.order_by(RunRecord.started_at.desc()).limit(limit).all())


class _AuditModule:
    @staticmethod
    def get_servers():
        return get_servers()

    @staticmethod
    def list_runs(*args, **kwargs):
        return list_runs(*args, **kwargs)


audit = _AuditModule()
