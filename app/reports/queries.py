"""Read-only aggregation queries that drive the M5 dashboards.

All shapes return JSON-friendly dicts so the FastAPI handlers can pass
them straight through to Chart.js.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Direction,
    DocStatus,
    DocumentExtraction,
    ProcessedDocument,
    Scope,
)


def _filed_query(scope_id: int | None, fy: int | None):
    stmt = (
        select(ProcessedDocument, DocumentExtraction)
        .join(
            DocumentExtraction,
            DocumentExtraction.document_id == ProcessedDocument.id,
            isouter=True,
        )
        .where(ProcessedDocument.status == DocStatus.filed)
    )
    if scope_id is not None:
        stmt = stmt.where(ProcessedDocument.scope_id == scope_id)
    if fy is not None:
        stmt = stmt.where(ProcessedDocument.financial_year == fy)
    return stmt


def monthly_totals(
    db: Session, *, scope_id: int | None = None, fy: int | None = None
) -> dict:
    """Income + expense + net per month for the given scope+FY."""
    stmt = _filed_query(scope_id, fy)
    months_income: dict[str, int] = defaultdict(int)
    months_expense: dict[str, int] = defaultdict(int)
    for doc, ext in db.execute(stmt):
        if not (ext and ext.amount_cents):
            continue
        bucket = (doc.doc_date or doc.filed_at or doc.created_at).strftime("%Y-%m")
        if doc.direction == Direction.income:
            months_income[bucket] += ext.amount_cents
        else:
            months_expense[bucket] += ext.amount_cents
    labels = sorted(set(months_income) | set(months_expense))
    return {
        "labels": labels,
        "income_cents": [months_income.get(m, 0) for m in labels],
        "expense_cents": [months_expense.get(m, 0) for m in labels],
        "net_cents": [
            months_income.get(m, 0) - months_expense.get(m, 0) for m in labels
        ],
    }


def by_category(
    db: Session,
    *,
    scope_id: int | None = None,
    fy: int | None = None,
    direction: Direction = Direction.expense,
) -> dict:
    stmt = _filed_query(scope_id, fy).where(ProcessedDocument.direction == direction)
    cat_lookup = {c.id: c.name for c in db.scalars(select(Category))}
    totals: dict[str, int] = defaultdict(int)
    for doc, ext in db.execute(stmt):
        if not (ext and ext.amount_cents):
            continue
        name = cat_lookup.get(doc.category_id, "Uncategorised")
        totals[name] += ext.amount_cents
    labels = sorted(totals, key=totals.get, reverse=True)
    return {
        "labels": labels,
        "totals_cents": [totals[k] for k in labels],
    }


def top_vendors(
    db: Session,
    *,
    scope_id: int | None = None,
    fy: int | None = None,
    direction: Direction = Direction.expense,
    limit: int = 10,
) -> dict:
    stmt = _filed_query(scope_id, fy).where(ProcessedDocument.direction == direction)
    totals: dict[str, int] = defaultdict(int)
    for _doc, ext in db.execute(stmt):
        if not (ext and ext.amount_cents and ext.counterparty_text):
            continue
        totals[ext.counterparty_text] += ext.amount_cents
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return {
        "labels": [k for k, _ in ranked],
        "totals_cents": [v for _, v in ranked],
    }


def vendor_trend(
    db: Session,
    *,
    scope_id: int | None = None,
    fy: int | None = None,
    direction: Direction = Direction.expense,
    top_n: int = 5,
) -> dict:
    """Per-vendor monthly totals for the top-N vendors by absolute amount."""
    stmt = _filed_query(scope_id, fy).where(ProcessedDocument.direction == direction)
    rows = list(db.execute(stmt))

    # Find the top N vendors by total
    overall: dict[str, int] = defaultdict(int)
    for _doc, ext in rows:
        if not (ext and ext.amount_cents and ext.counterparty_text):
            continue
        overall[ext.counterparty_text] += ext.amount_cents
    top_names = [k for k, _ in sorted(overall.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]

    # Per-vendor monthly buckets
    months: set[str] = set()
    series: dict[str, dict[str, int]] = {name: defaultdict(int) for name in top_names}
    for doc, ext in rows:
        if not (ext and ext.amount_cents and ext.counterparty_text):
            continue
        if ext.counterparty_text not in series:
            continue
        bucket = (doc.doc_date or doc.filed_at or doc.created_at).strftime("%Y-%m")
        series[ext.counterparty_text][bucket] += ext.amount_cents
        months.add(bucket)

    labels = sorted(months)
    return {
        "labels": labels,
        "datasets": [
            {
                "name": name,
                "totals_cents": [series[name].get(m, 0) for m in labels],
            }
            for name in top_names
        ],
    }


@dataclass(frozen=True)
class TableRow:
    doc_id: int
    drive_file_id: str | None
    doc_date: str | None
    counterparty: str | None
    category: str | None
    direction: str
    amount_cents: int | None
    currency: str | None


def table(
    db: Session,
    *,
    scope_id: int | None = None,
    fy: int | None = None,
) -> list[TableRow]:
    stmt = _filed_query(scope_id, fy).order_by(
        ProcessedDocument.doc_date.desc().nullslast(),
        ProcessedDocument.id.desc(),
    )
    cat_lookup = {c.id: c.name for c in db.scalars(select(Category))}
    rows: list[TableRow] = []
    for doc, ext in db.execute(stmt):
        rows.append(
            TableRow(
                doc_id=doc.id,
                drive_file_id=doc.drive_file_id,
                doc_date=doc.doc_date.isoformat() if doc.doc_date else None,
                counterparty=ext.counterparty_text if ext else None,
                category=cat_lookup.get(doc.category_id) if doc.category_id else None,
                direction=doc.direction.value,
                amount_cents=ext.amount_cents if ext else None,
                currency=ext.currency if ext else None,
            )
        )
    return rows


def cross_scope_overview(db: Session, *, fy: int | None = None) -> dict:
    """For /overview: monthly net per scope + totals."""
    stmt = _filed_query(scope_id=None, fy=fy)
    scopes = {s.id: s for s in db.scalars(select(Scope))}
    by_scope_month: dict[int, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"income": 0, "expense": 0})
    )
    for doc, ext in db.execute(stmt):
        if not (ext and ext.amount_cents and doc.scope_id is not None):
            continue
        bucket = (doc.doc_date or doc.filed_at or doc.created_at).strftime("%Y-%m")
        side = "income" if doc.direction == Direction.income else "expense"
        by_scope_month[doc.scope_id][bucket][side] += ext.amount_cents

    months: set[str] = set()
    for scope_id, month_map in by_scope_month.items():
        months.update(month_map.keys())
    labels = sorted(months)

    datasets = []
    for scope_id, month_map in by_scope_month.items():
        scope = scopes.get(scope_id)
        if scope is None:
            continue
        net = [
            month_map.get(m, {"income": 0, "expense": 0})["income"]
            - month_map.get(m, {"income": 0, "expense": 0})["expense"]
            for m in labels
        ]
        datasets.append({"scope": scope.name, "net_cents": net})

    return {"labels": labels, "datasets": datasets}
