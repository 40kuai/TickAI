"""SQLAlchemy ORM models for OpsTicket."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    tags: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ssh_credential_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ssh_credentials.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    runs: Mapped[list["RunRecord"]] = relationship(back_populates="server")
    ssh_credential: Mapped[Optional["SSHCredential"]] = relationship()

    __table_args__ = (
        Index("ix_servers_is_active", "is_active"),
    )

    def to_dict(self, redact_password: bool = True) -> dict:
        tags_list = []
        if self.tags:
            tags_list = [t.strip() for t in self.tags.split(",") if t.strip()]
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "tags": tags_list,
            "notes": self.notes,
            "is_active": self.is_active,
            "ssh_credential_id": self.ssh_credential_id,
            "ssh_credential_name": self.ssh_credential.name if self.ssh_credential else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }


class SSHCredential(Base):
    """Reusable SSH credentials that can be bound to servers."""
    __tablename__ = "ssh_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(64), default="root")
    auth_type: Mapped[str] = mapped_column(String(16), default="password")  # password|key
    password: Mapped[str] = mapped_column(String(255), default="")
    key_content: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(String(255), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self, redact_password: bool = True) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "password": "***" if redact_password else self.password,
            "key_content": "***" if (redact_password and self.key_content) else self.key_content,
            "description": self.description,
            "is_default": self.is_default,
        }

    def to_connect_args(self) -> dict:
        """Return args dict for SSH handler functions."""
        args = {"port": self.port, "username": self.username}
        if self.auth_type == "key" and self.key_content:
            args["key_content"] = self.key_content
        else:
            args["password"] = self.password or ""
        return args


class RunRecord(Base):
    __tablename__ = "run_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("servers.id"), nullable=True)

    command: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))  # success / failed / timeout / ssh_error
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    structured_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    triggered_by: Mapped[str] = mapped_column(String(32), default="user_button")
    triggered_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    server: Mapped["Server"] = relationship(back_populates="runs")

    __table_args__ = (
        Index("ix_run_history_server_id", "server_id"),
        Index("ix_run_history_started_at", "started_at"),
        Index("ix_run_history_status", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "server_id": self.server_id,
            "server_name": self.server.name if self.server else None,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "structured_result": self.structured_result,
            "triggered_by": self.triggered_by,
            "triggered_context": self.triggered_context,
        }


class Conversation(Base):
    __tablename__ = "llm_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    total_runs: Mapped[int] = mapped_column(Integer, default=0)


class SkillOutcome(Base):
    """One execution of a skill: what it found, what user did with it.

    The lifecycle is:
      1. Skill runs (LLM produces findings_json)
      2. User reviews and marks decision: accepted / rejected / pending
      3. Later: user marks outcome_effect: improved / no_change / worse
      4. Evolver reads this table to refine skills
    """
    __tablename__ = "skill_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(255), index=True)
    skill_version: Mapped[int] = mapped_column(Integer, default=1)
    cluster_context: Mapped[str] = mapped_column(String(255), default="")
    triggered_by: Mapped[str] = mapped_column(String(64), default="user")
    # user | scheduled | llm
    run_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # LLM-produced findings (JSON array of structured findings)
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    findings_summary: Mapped[str] = mapped_column(Text, default="")

    # User feedback loop
    user_decision: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | accepted | rejected
    decision_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_notes: Mapped[str] = mapped_column(Text, default="")

    # Outcome effect — measured later
    outcome_effect: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # improved | no_change | worse
    measured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    effect_notes: Mapped[str] = mapped_column(Text, default="")


class SkillVersion(Base):
    """Version history of a skill .md file.

    Like a git commit: stores full content + diff vs previous + reason for change.
    Used by the evolver to show what changed and to allow rollback.
    """
    __tablename__ = "skill_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String(64), default="manual")
    # initial | manual | auto_evolve
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    """System users for authentication."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UserSession(Base):
    """Login sessions for UI authentication."""
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid4
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_user_sessions_expires_at", "expires_at"),
    )


class SyncConfig(Base):
    """External host sync configuration (single-row table)."""
    __tablename__ = "sync_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    api_url: Mapped[str] = mapped_column(Text, default="")
    auth_type: Mapped[str] = mapped_column(String(16), default="none")  # none|basic|bearer
    auth_username: Mapped[str] = mapped_column(String(128), default="")
    auth_password: Mapped[str] = mapped_column(String(255), default="")
    api_token: Mapped[str] = mapped_column(Text, default="")
    response_path: Mapped[str] = mapped_column(String(255), default="")
    timeout: Mapped[int] = mapped_column(Integer, default=30)
    # JSON string: {"name": "hostname", "host": "ip", "tags": "labels", "notes": "desc"}
    field_mapping: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self, redact_secrets: bool = True) -> dict:
        import json as _json
        mapping = {}
        if self.field_mapping:
            try:
                mapping = _json.loads(self.field_mapping)
            except (ValueError, TypeError):
                mapping = {}
        auth_password = self.auth_password
        api_token = self.api_token
        if redact_secrets:
            auth_password = "***" if auth_password else ""
            api_token = "***" if api_token else ""
        return {
            "api_url": self.api_url,
            "auth_type": self.auth_type,
            "auth_username": self.auth_username,
            "auth_password": auth_password,
            "api_token": api_token,
            "response_path": self.response_path,
            "timeout": self.timeout,
            "field_mapping": mapping,
            "enabled": self.enabled,
        }


class SelfHealAction(Base):
    """一次自愈动作：探测→分级→执行/审批→验证 的完整审计。

    状态机:
      pending → approved → executed → verified            (低危直接 executing→executed)
              ↘ rejected
      pending → approved → executed → verification_failed (验证未通过)
      任意执行阶段失败 → failed
    success = (执行成功 && 验证通过) 双条件。
    """
    __tablename__ = "selfheal_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"))
    scene: Mapped[str] = mapped_column(String(32))       # process_restart / disk_clean / cache_clean
    target: Mapped[str] = mapped_column(String(255))     # 服务名 / 挂载点 / drop_caches 模式
    severity: Mapped[str] = mapped_column(String(8))     # low / high
    action_name: Mapped[str] = mapped_column(String(64)) # 模板标识
    rendered_command: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 渲染后精确命令(审计留痕); 渲染失败为 None
    status: Mapped[str] = mapped_column(String(24), default="pending")
    # pending / approved / rejected / executing / executed / failed / verified / verification_failed
    triggered_by: Mapped[str] = mapped_column(String(24), default="user")  # user / dialog
    approver: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # JSON
    verification_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    grade_reasons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # 分级理由 JSON
    plan_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # AI 策略批次 ID
    success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    server: Mapped["Server"] = relationship()

    __table_args__ = (
        Index("ix_selfheal_actions_status", "status"),
        Index("ix_selfheal_actions_server_id", "server_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "server_id": self.server_id,
            "server_name": self.server.name if self.server else None,
            "scene": self.scene,
            "target": self.target,
            "severity": self.severity,
            "action_name": self.action_name,
            "rendered_command": self.rendered_command,
            "status": self.status,
            "triggered_by": self.triggered_by,
            "approver": self.approver,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "execution_result": self.execution_result,
            "verification_result": self.verification_result,
            "grade_reasons": self.grade_reasons,
            "plan_id": self.plan_id,
            "success": self.success,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
