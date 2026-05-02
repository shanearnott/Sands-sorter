"""importer dry-run flag.

Revision ID: 202604290005
Revises: 202604290004
Create Date: 2026-04-29

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290005"
down_revision = "202604290004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column(
            "dry_run", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "dry_run")
