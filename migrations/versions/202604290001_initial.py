"""initial schema

Revision ID: 202604290001
Revises:
Create Date: 2026-04-29

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    match_type = sa.Enum(
        "contains", "regex", "sender_email", "filename", name="match_type"
    )
    rule_source = sa.Enum("manual", "learned_from_reassign", name="rule_source")
    source_kind = sa.Enum("gmail", "dropbox", name="source_kind")
    classifier = sa.Enum("rule", "llm", "manual", "unsorted", name="classifier")
    doc_status = sa.Enum("filed", "unsorted", "error", name="doc_status")

    bind = op.get_bind()
    for enum in (match_type, rule_source, source_kind, classifier, doc_status):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "properties",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("drive_folder_id", sa.String(120)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "vendors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True),
        sa.Column("default_property_id", sa.Integer, sa.ForeignKey("properties.id")),
        sa.Column("default_category_id", sa.Integer, sa.ForeignKey("categories.id")),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("match_type", match_type, nullable=False),
        sa.Column("pattern", sa.String(500), nullable=False),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id"), nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("vendor_id", sa.Integer, sa.ForeignKey("vendors.id")),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("source", rule_source, nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", source_kind, nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("oauth_secret_name", sa.String(200)),
        sa.Column("cursor", sa.String(500)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "label", name="uq_source_kind_label"),
    )

    op.create_table(
        "processed_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id")),
        sa.Column("source_msg_ref", sa.String(500)),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("ocr_text", sa.Text),
        sa.Column("classifier", classifier, nullable=False),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("rules.id")),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id")),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id")),
        sa.Column("year", sa.Integer),
        sa.Column("drive_file_id", sa.String(120)),
        sa.Column("drive_path", sa.String(1000)),
        sa.Column("confidence", sa.Float),
        sa.Column("status", doc_status, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("filed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "reassignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("processed_documents.id"),
            nullable=False,
        ),
        sa.Column("old_property_id", sa.Integer, sa.ForeignKey("properties.id")),
        sa.Column("old_category_id", sa.Integer, sa.ForeignKey("categories.id")),
        sa.Column(
            "new_property_id", sa.Integer, sa.ForeignKey("properties.id"), nullable=False
        ),
        sa.Column(
            "new_category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False
        ),
        sa.Column("created_rule_id", sa.Integer, sa.ForeignKey("rules.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "daily_summaries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("document_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unsorted_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("message_id", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("daily_summaries")
    op.drop_table("reassignments")
    op.drop_table("processed_documents")
    op.drop_table("sources")
    op.drop_table("rules")
    op.drop_table("vendors")
    op.drop_table("categories")
    op.drop_table("properties")
    bind = op.get_bind()
    for name in ("doc_status", "classifier", "source_kind", "rule_source", "match_type"):
        sa.Enum(name=name).drop(bind, checkfirst=True)
