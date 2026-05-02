"""scope kinds refactor: car/life -> personal/entity, seed real-world inventory.

Revision ID: 202604290004
Revises: 202604290003
Create Date: 2026-04-29

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202604290004"
down_revision = "202604290003"
branch_labels = None
depends_on = None


SEED_ROWS = [
    # (kind, name, country)
    ("property", "14A Wave", "AU"),
    ("property", "14B Wave", "AU"),
    ("property", "73 Goodwin", "AU"),
    ("property", "81 Goodwin", "AU"),
    ("property", "141 Sydney", "AU"),
    ("property", "18230 Santa Arabella", "US"),
    ("property", "73451 Royal Palm", "US"),
    ("property", "Audi S5", "AU"),
    ("property", "Tesla S", "US"),
    ("entity", "SANDS", "AU"),
    ("entity", "Farmout", "AU"),
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. Drop any rows that hold a `kind` value that won't survive the new ENUM.
    op.execute("DELETE FROM import_items WHERE proposed_scope_kind = 'car'")
    op.execute("DELETE FROM scopes WHERE kind = 'car'")

    # 2. Mutate the ENUM type.
    if is_postgres:
        # Rename-and-replace dance because Postgres can't add/remove values
        # mid-flight without dropping the type.
        op.execute("ALTER TYPE scope_kind RENAME TO scope_kind_old")
        op.execute("CREATE TYPE scope_kind AS ENUM ('property', 'personal', 'entity')")

        op.execute(
            "ALTER TABLE scopes "
            "ALTER COLUMN kind TYPE scope_kind "
            "USING (CASE kind::text WHEN 'life' THEN 'personal' "
            "ELSE kind::text END)::scope_kind"
        )
        op.execute(
            "ALTER TABLE import_items "
            "ALTER COLUMN proposed_scope_kind TYPE scope_kind "
            "USING (CASE proposed_scope_kind::text WHEN 'life' THEN 'personal' "
            "ELSE proposed_scope_kind::text END)::scope_kind"
        )
        op.execute("DROP TYPE scope_kind_old")
    else:
        # SQLite stores enums as VARCHAR with a CHECK constraint; the easiest
        # path is to UPDATE rows in place. The CHECK constraint will be
        # recreated when SQLAlchemy reflects the model.
        op.execute("UPDATE scopes SET kind = 'personal' WHERE kind = 'life'")
        op.execute(
            "UPDATE import_items SET proposed_scope_kind = 'personal' "
            "WHERE proposed_scope_kind = 'life'"
        )

    # 3. Singleton row label refresh.
    op.execute(
        "UPDATE scopes SET name = 'Personal' "
        "WHERE kind = 'personal' AND name = 'Life'"
    )

    # 4. Seed real-world inventory. Idempotent thanks to uq_scope_kind_name.
    insert_stmt = sa.text(
        "INSERT INTO scopes (kind, name, country, active) "
        "VALUES (:kind, :name, :country, TRUE) "
        "ON CONFLICT (kind, name) DO NOTHING"
    )
    if not is_postgres:
        insert_stmt = sa.text(
            "INSERT OR IGNORE INTO scopes (kind, name, country, active) "
            "VALUES (:kind, :name, :country, 1)"
        )
    for kind, name, country in SEED_ROWS:
        bind.execute(insert_stmt, {"kind": kind, "name": name, "country": country})


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Remove seeded rows (best-effort — only the names we know we added).
    for _kind, name, _country in SEED_ROWS:
        bind.execute(
            sa.text("DELETE FROM scopes WHERE name = :name"), {"name": name}
        )

    # Restore the Life singleton label.
    op.execute(
        "UPDATE scopes SET name = 'Life' "
        "WHERE kind = 'personal' AND name = 'Personal'"
    )

    if is_postgres:
        op.execute("ALTER TYPE scope_kind RENAME TO scope_kind_old")
        op.execute("CREATE TYPE scope_kind AS ENUM ('property', 'car', 'life')")

        op.execute(
            "ALTER TABLE scopes "
            "ALTER COLUMN kind TYPE scope_kind "
            "USING (CASE kind::text WHEN 'personal' THEN 'life' "
            "ELSE kind::text END)::scope_kind"
        )
        op.execute(
            "ALTER TABLE import_items "
            "ALTER COLUMN proposed_scope_kind TYPE scope_kind "
            "USING (CASE proposed_scope_kind::text WHEN 'personal' THEN 'life' "
            "ELSE proposed_scope_kind::text END)::scope_kind"
        )
        op.execute("DROP TYPE scope_kind_old")
    else:
        op.execute("UPDATE scopes SET kind = 'life' WHERE kind = 'personal'")
        op.execute(
            "UPDATE import_items SET proposed_scope_kind = 'life' "
            "WHERE proposed_scope_kind = 'personal'"
        )
