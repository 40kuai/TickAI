"""0002_ssh_credential_refactor

SSH credential independence: remove port/username/password from servers,
add ssh_credentials table, add sync_config table, make run_history.server_id nullable.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply SSH credential refactor schema changes."""

    # 1. Create ssh_credentials table
    op.create_table(
        "ssh_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("auth_type", sa.String(length=16), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("key_content", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # 2. Create sync_config table
    op.create_table(
        "sync_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_url", sa.String(length=512), nullable=False),
        sa.Column("auth_type", sa.String(length=16), nullable=False),
        sa.Column("auth_username", sa.String(length=128), nullable=False),
        sa.Column("auth_password", sa.String(length=255), nullable=False),
        sa.Column("api_token", sa.String(length=512), nullable=False),
        sa.Column("response_path", sa.String(length=255), nullable=False),
        sa.Column("timeout", sa.Integer(), nullable=False),
        sa.Column("field_mapping", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3. Add ssh_credential_id to servers
    op.add_column(
        "servers",
        sa.Column("ssh_credential_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_servers_ssh_credential_id",
        "servers",
        "ssh_credentials",
        ["ssh_credential_id"],
        ["id"],
    )

    # 4. Remove port, username, password from servers
    op.drop_column("servers", "port")
    op.drop_column("servers", "username")
    op.drop_column("servers", "password")

    # 5. Make run_history.server_id nullable
    op.alter_column(
        "run_history",
        "server_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    """Revert SSH credential refactor."""
    # Restore run_history.server_id to NOT NULL
    op.alter_column(
        "run_history",
        "server_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # Restore servers columns
    op.add_column("servers", sa.Column("port", sa.Integer(), nullable=False, server_default="22"))
    op.add_column("servers", sa.Column("username", sa.String(length=64), nullable=False, server_default="root"))
    op.add_column("servers", sa.Column("password", sa.String(length=255), nullable=False, server_default=""))

    # Drop ssh_credential_id from servers
    op.drop_constraint("fk_servers_ssh_credential_id", "servers", type_="foreignkey")
    op.drop_column("servers", "ssh_credential_id")

    # Drop tables
    op.drop_table("sync_config")
    op.drop_table("ssh_credentials")
