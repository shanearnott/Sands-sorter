"""importer tables: import_jobs and import_items.

Revision ID: 202604290003
Revises: 202604290002
Create Date: 2026-04-29

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290003"
down_revision = "202604290002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    job_status = sa.Enum(
        "pending", "running", "paused", "done", "cancelled", "error",
        name="import_job_status",
    )
    item_status = sa.Enum(
        "pending", "awaiting", "copied", "skipped", "error",
        name="import_item_status",
    )
    source_kind = sa.Enum("drive", "local", name="import_source_kind")

    bind = op.get_bind()
    for enum in (job_status, item_status, source_kind):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_kind", source_kind, nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("status", job_status, nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("total_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("copied_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("awaiting_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "import_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer,
            sa.ForeignKey("import_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_path", sa.String(1000), nullable=False),
        sa.Column("source_file_id", sa.String(120)),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("ocr_text", sa.Text),
        sa.Column("extracted_amount_cents", sa.Integer),
        sa.Column("extracted_currency", sa.String(3)),
        sa.Column("extracted_doc_date", sa.Date),
        sa.Column("extracted_due_date", sa.Date),
        sa.Column("status", item_status, nullable=False, server_default="pending"),
        sa.Column("proposed_scope_id", sa.Integer, sa.ForeignKey("scopes.id")),
        sa.Column("proposed_scope_name", sa.String(120)),
        sa.Column(
            "proposed_scope_kind",
            sa.Enum("property", "car", "life", name="scope_kind", create_type=False),
        ),
        sa.Column("proposed_category_id", sa.Integer, sa.ForeignKey("categories.id")),
        sa.Column("proposed_category_name", sa.String(120)),
        sa.Column(
            "proposed_direction",
            sa.Enum("expense", "income", name="direction", create_type=False),
        ),
        sa.Column("proposed_fy", sa.Integer),
        sa.Column("proposed_drive_path", sa.String(1000)),
        sa.Column("counterparty", sa.String(300)),
        sa.Column("confidence", sa.Float),
        sa.Column("llm_reasoning", sa.Text),
        sa.Column("decision_doc_id", sa.Integer, sa.ForeignKey("processed_documents.id")),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_import_items_job_status", "import_items", ["job_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_import_items_job_status", table_name="import_items")
    op.drop_table("import_items")
    op.drop_table("import_jobs")
    bind = op.get_bind()
    for name in ("import_item_status", "import_job_status", "import_source_kind"):
        sa.Enum(name=name).drop(bind, checkfirst=True)
