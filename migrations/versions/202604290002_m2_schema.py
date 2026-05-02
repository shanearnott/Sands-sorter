"""M2 schema additions: country, direction, doc_date/financial_year, document_extractions, summary_runs rename.

Revision ID: 202604290002
Revises: 202604290001
Create Date: 2026-04-29

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290002"
down_revision = "202604290001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    from sqlalchemy.dialects.postgresql import ENUM as PgEnum
    scope_country = PgEnum("AU", "US", name="scope_country", create_type=False)
    direction = PgEnum("expense", "income", name="direction", create_type=False)
    scope_country.create(bind, checkfirst=True)
    direction.create(bind, checkfirst=True)

    # scopes.country
    op.add_column(
        "scopes",
        sa.Column("country", scope_country, nullable=False, server_default="AU"),
    )

    # categories.default_direction
    op.add_column("categories", sa.Column("default_direction", direction, nullable=True))

    # vendors.default_direction
    op.add_column("vendors", sa.Column("default_direction", direction, nullable=True))

    # rules.direction
    op.add_column("rules", sa.Column("direction", direction, nullable=True))

    # processed_documents: replace `year` with doc_date + financial_year, add direction
    op.add_column(
        "processed_documents",
        sa.Column("direction", direction, nullable=False, server_default="expense"),
    )
    op.add_column("processed_documents", sa.Column("doc_date", sa.Date, nullable=True))
    op.add_column("processed_documents", sa.Column("financial_year", sa.Integer, nullable=True))
    # Carry old calendar `year` into `financial_year` as a best-effort starting point.
    op.execute("UPDATE processed_documents SET financial_year = year WHERE year IS NOT NULL")
    op.drop_column("processed_documents", "year")

    # reassignments: track direction changes too
    op.add_column("reassignments", sa.Column("old_direction", direction, nullable=True))
    op.add_column("reassignments", sa.Column("new_direction", direction, nullable=True))

    # document_extractions
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("processed_documents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("counterparty_text", sa.String(300)),
        sa.Column("amount_cents", sa.Integer),
        sa.Column("currency", sa.String(3)),
        sa.Column("doc_date", sa.Date),
        sa.Column("due_date", sa.Date),
        sa.Column("account_number", sa.String(120)),
        sa.Column("raw_json", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # daily_summaries -> summary_runs
    op.rename_table("daily_summaries", "summary_runs")
    op.add_column("summary_runs", sa.Column("cadence", sa.String(20), nullable=True))
    op.add_column(
        "summary_runs", sa.Column("period_start", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "summary_runs", sa.Column("period_end", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "summary_runs",
        sa.Column("income_total_cents", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "summary_runs",
        sa.Column("expense_total_cents", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("summary_runs", "expense_total_cents")
    op.drop_column("summary_runs", "income_total_cents")
    op.drop_column("summary_runs", "period_end")
    op.drop_column("summary_runs", "period_start")
    op.drop_column("summary_runs", "cadence")
    op.rename_table("summary_runs", "daily_summaries")

    op.drop_table("document_extractions")

    op.drop_column("reassignments", "new_direction")
    op.drop_column("reassignments", "old_direction")

    op.add_column("processed_documents", sa.Column("year", sa.Integer, nullable=True))
    op.execute("UPDATE processed_documents SET year = financial_year")
    op.drop_column("processed_documents", "financial_year")
    op.drop_column("processed_documents", "doc_date")
    op.drop_column("processed_documents", "direction")

    op.drop_column("rules", "direction")
    op.drop_column("vendors", "default_direction")
    op.drop_column("categories", "default_direction")
    op.drop_column("scopes", "country")

    bind = op.get_bind()
    sa.Enum(name="direction").drop(bind, checkfirst=True)
    sa.Enum(name="scope_country").drop(bind, checkfirst=True)
