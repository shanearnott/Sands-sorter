"""app_config table for runtime-editable settings

Revision ID: 202604290007
Revises: 202604290006
Create Date: 2026-05-02

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290007"
down_revision = "202604290006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_config")
