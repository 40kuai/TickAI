"""0001_initial_schema

Initial database schema matching hermes/data/models.py.

Revision ID: 0001
Revises: 
Create Date: 2026-01-01 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all tables - matches the actual models.py."""
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("tags", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_servers_is_active", "servers", ["is_active"])

    op.create_table(
        "llm_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("messages_json", sa.Text(), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "skill_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(length=255), nullable=False),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("cluster_context", sa.String(length=255), nullable=False),
        sa.Column("triggered_by", sa.String(length=64), nullable=False),
        sa.Column("run_at", sa.DateTime(), nullable=False),
        sa.Column("findings_json", sa.Text(), nullable=False),
        sa.Column("findings_summary", sa.Text(), nullable=False),
        sa.Column("user_decision", sa.String(length=32), nullable=False),
        sa.Column("decision_at", sa.DateTime(), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=False),
        sa.Column("outcome_effect", sa.String(length=32), nullable=True),
        sa.Column("measured_at", sa.DateTime(), nullable=True),
        sa.Column("effect_notes", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_skill_outcomes_skill_name"),
        "skill_outcomes",
        ["skill_name"],
        unique=False,
    )

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("skill_name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_skill_versions_skill_name"),
        "skill_versions",
        ["skill_name"],
        unique=False,
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_sessions_expires_at"),
        "user_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_user_id"),
        "user_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "run_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("command", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("stdout", sa.Text(), nullable=False),
        sa.Column("stderr", sa.Text(), nullable=False),
        sa.Column("structured_result", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=32), nullable=False),
        sa.Column("triggered_context", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_run_history_server_id"),
        "run_history",
        ["server_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_history_started_at"),
        "run_history",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_history_status"),
        "run_history",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_index(op.f("ix_run_history_status"), table_name="run_history")
    op.drop_index(op.f("ix_run_history_started_at"), table_name="run_history")
    op.drop_index(op.f("ix_run_history_server_id"), table_name="run_history")
    op.drop_table("run_history")

    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")

    op.drop_index(op.f("ix_skill_versions_skill_name"), table_name="skill_versions")
    op.drop_table("skill_versions")

    op.drop_index(op.f("ix_skill_outcomes_skill_name"), table_name="skill_outcomes")
    op.drop_table("skill_outcomes")

    op.drop_table("llm_conversations")

    op.drop_index("ix_servers_is_active", table_name="servers")
    op.drop_table("servers")
