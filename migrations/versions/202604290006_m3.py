"""M3: pdf_passwords vault + sources.body_allowlist + last_polled tracking.

Revision ID: 202604290006
Revises: 202604290005
Create Date: 2026-04-29

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290006"
down_revision = "202604290005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pdf_passwords",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("password", sa.String(500), nullable=False),
        sa.Column("sender_match", sa.String(200)),
        sa.Column("filename_match", sa.String(200)),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.add_column("sources", sa.Column("body_allowlist", sa.JSON))
    op.add_column(
        "sources",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("sources", sa.Column("last_error", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "last_error")
    op.drop_column("sources", "last_polled_at")
    op.drop_column("sources", "body_allowlist")
    op.drop_table("pdf_passwords")
