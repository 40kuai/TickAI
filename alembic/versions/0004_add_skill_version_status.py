"""0004_add_skill_version_status

Add status column to skill_versions (P2 技能进化门禁: 候选版本 pending/active/rolled_back).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add status to skill_versions (default active, 存量记录即线上版本)."""
    op.add_column(
        "skill_versions",
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="active"),
    )


def downgrade() -> None:
    """Drop status from skill_versions."""
    op.drop_column("skill_versions", "status")
