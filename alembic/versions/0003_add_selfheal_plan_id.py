"""0003_add_selfheal_plan_id

Add plan_id column to selfheal_actions (AI 清理策略批次归组, Task 4/日志清理双通道).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add plan_id to selfheal_actions (nullable, AI 策略批次 ID)."""
    op.add_column(
        "selfheal_actions",
        sa.Column("plan_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Drop plan_id from selfheal_actions."""
    op.drop_column("selfheal_actions", "plan_id")
