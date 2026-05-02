from datetime import date

from app.models import Direction, DocStatus, ProcessedDocument
from app.web.scope_routes import group_by_fy


def _doc(fy: int | None, doc_id: int) -> ProcessedDocument:
    return ProcessedDocument(
        id=doc_id,
        sha256=f"abc{doc_id}",
        original_filename=f"f{doc_id}.pdf",
        classifier="manual",
        direction=Direction.expense,
        status=DocStatus.filed,
        financial_year=fy,
        doc_date=date(2025, 9, 1) if fy else None,
    )


def test_group_by_fy_orders_descending():
    docs = [_doc(2024, 1), _doc(2026, 2), _doc(2025, 3), _doc(2026, 4)]
    groups = group_by_fy(docs)
    assert [g.fy for g in groups] == [2026, 2025, 2024]
    assert [g.label for g in groups] == ["FY2026", "FY2025", "FY2024"]
    assert [d.id for d in groups[0].docs] == [2, 4]


def test_group_by_fy_buckets_unknowns_under_zero():
    docs = [_doc(None, 9), _doc(2026, 1)]
    groups = group_by_fy(docs)
    assert groups[0].fy == 2026
    assert groups[0].label == "FY2026"
    assert groups[1].fy == 0
    assert groups[1].label == "Unknown FY"


def test_group_by_fy_empty():
    assert group_by_fy([]) == []
