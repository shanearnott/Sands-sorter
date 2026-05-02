from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Category,
    Direction,
    DocStatus,
    DocumentExtraction,
    ProcessedDocument,
    Scope,
    ScopeCountry,
    ScopeKind,
)
from app.reports import export, queries


def _setup() -> tuple[Session, Scope, Category]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    scope = Scope(kind=ScopeKind.property, name="Beach House", country=ScopeCountry.AU)
    cat = Category(name="Electricity")
    db.add_all([scope, cat])
    db.commit()
    return db, scope, cat


def _add_doc(db, scope, cat, *, year, month, amount_cents, direction=Direction.expense):
    d = ProcessedDocument(
        sha256=f"sha-{year}-{month}-{amount_cents}-{direction.value}",
        original_filename="x.pdf",
        classifier="rule",
        scope_id=scope.id,
        category_id=cat.id,
        direction=direction,
        doc_date=date(year, month, 1),
        financial_year=year if year >= 2026 else 2025,
        status=DocStatus.filed,
    )
    db.add(d)
    db.flush()
    db.add(
        DocumentExtraction(
            document_id=d.id,
            counterparty_text="Origin Energy" if direction == Direction.expense else "Airbnb",
            amount_cents=amount_cents,
            currency="AUD",
            doc_date=date(year, month, 1),
        )
    )
    db.commit()
    return d


def test_monthly_totals_groups_income_and_expense():
    db, scope, cat = _setup()
    _add_doc(db, scope, cat, year=2025, month=8, amount_cents=8900)
    _add_doc(db, scope, cat, year=2025, month=8, amount_cents=2000, direction=Direction.income)
    _add_doc(db, scope, cat, year=2025, month=9, amount_cents=12000)
    out = queries.monthly_totals(db, scope_id=scope.id)
    assert out["labels"] == ["2025-08", "2025-09"]
    assert out["income_cents"] == [2000, 0]
    assert out["expense_cents"] == [8900, 12000]
    assert out["net_cents"] == [-6900, -12000]


def test_top_vendors_ranks_by_total():
    db, scope, cat = _setup()
    _add_doc(db, scope, cat, year=2025, month=8, amount_cents=5000)
    _add_doc(db, scope, cat, year=2025, month=9, amount_cents=15000)
    _add_doc(db, scope, cat, year=2025, month=9, amount_cents=4000, direction=Direction.income)
    out = queries.top_vendors(db, scope_id=scope.id)
    assert out["labels"] == ["Origin Energy"]
    assert out["totals_cents"] == [20000]


def test_export_csv_round_trip():
    db, scope, cat = _setup()
    _add_doc(db, scope, cat, year=2025, month=8, amount_cents=8900)
    rows = queries.table(db, scope_id=scope.id)
    csv_bytes = export.to_csv(rows)
    text = csv_bytes.decode("utf-8")
    assert "Origin Energy" in text
    assert "89.00" in text
    assert text.splitlines()[0].startswith("doc_id,doc_date")
