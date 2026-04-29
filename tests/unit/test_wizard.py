from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.classifier.llm import LLMSuggestion
from app.drive.uploader import DriveUpload
from app.extraction.invoice import InvoiceFields
from app.importer import wizard
from app.importer.sources import SourceItem
from app.models import (
    Base,
    Category,
    Direction,
    ImportItem,
    ImportItemStatus,
    ImportJob,
    ImportSourceKind,
    ProcessedDocument,
    Scope,
    ScopeCountry,
    ScopeKind,
)


class FakeLLM:
    def __init__(self, suggestion: LLMSuggestion):
        self._s = suggestion

    def classify(self, **kwargs):
        return self._s


@dataclass
class FakeExtractor:
    fields: InvoiceFields

    def extract(self, *, data: bytes, mime_type: str) -> InvoiceFields:
        return self.fields


class FakeUploader:
    def __init__(self):
        self.uploads: list[tuple] = []

    def upload(self, *, path, data, mime_type=None):
        self.uploads.append((str(path), len(data), mime_type))
        return DriveUpload(
            file_id=f"drive-{len(self.uploads)}",
            drive_path=str(path),
            web_view_link=None,
        )


def _setup() -> tuple[Session, Scope, Category, ImportJob]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    scope = Scope(kind=ScopeKind.property, name="Beach House", country=ScopeCountry.AU)
    cat = Category(name="Electricity")
    db.add_all([scope, cat])
    db.commit()
    job = ImportJob(source_kind=ImportSourceKind.local, source_ref="/tmp/x")
    db.add(job)
    db.commit()
    return db, scope, cat, job


class FakeSource:
    def __init__(self, items):
        self._items = items

    def iter_items(self):
        yield from self._items


def _src(path: str) -> SourceItem:
    return SourceItem(
        relative_path=path,
        filename=path.split("/")[-1],
        mime_type="application/pdf",
        modified_time=None,
        bytes_loader=lambda: b"BYTES",
    )


def test_enqueue_items_inserts_pending_rows():
    db, _scope, _cat, job = _setup()
    added = wizard.enqueue_items(
        db,
        job,
        FakeSource([_src("Beach House/Electricity/2024/origin.pdf"), _src("x.pdf")]),
    )
    assert added == 2
    assert job.total_count == 2
    rows = db.query(ImportItem).order_by(ImportItem.id).all()
    assert [r.source_path for r in rows] == [
        "Beach House/Electricity/2024/origin.pdf",
        "x.pdf",
    ]
    assert all(r.status == ImportItemStatus.pending for r in rows)


def test_enqueue_items_idempotent():
    db, _, _, job = _setup()
    items = [_src("x.pdf")]
    wizard.enqueue_items(db, job, FakeSource(items))
    added2 = wizard.enqueue_items(db, job, FakeSource(items))
    assert added2 == 0
    assert db.query(ImportItem).count() == 1


def test_process_item_auto_files_high_confidence_match():
    db, scope, cat, job = _setup()
    wizard.enqueue_items(db, job, FakeSource([_src("origin.pdf")]))
    item = db.query(ImportItem).first()

    fields = InvoiceFields(
        text="ORIGIN ENERGY invoice",
        counterparty="Origin Energy",
        amount_cents=8900,
        currency="AUD",
        doc_date=date(2025, 9, 1),
        due_date=None,
        account_number=None,
        raw_entities={},
    )
    llm = FakeLLM(
        LLMSuggestion(
            scope_name="Beach House",
            scope_kind_hint=None,
            category_name="Electricity",
            direction=Direction.expense,
            counterparty="Origin Energy",
            confidence=0.95,
            reasoning="ok",
            new_scope=False,
            new_category=False,
        )
    )
    uploader = FakeUploader()

    status = wizard.process_item(
        db,
        item,
        fetch_bytes=lambda _i: b"PDFBYTES",
        extractor=FakeExtractor(fields),
        llm=llm,
        uploader=uploader,
    )
    assert status == ImportItemStatus.copied
    assert len(uploader.uploads) == 1
    drive_path, _size, _mime = uploader.uploads[0]
    assert drive_path == "Properties/Beach House/Electricity/FY2026/origin.pdf"

    db.refresh(item)
    assert item.decision_doc_id is not None
    assert job.copied_count == 1

    doc = db.get(ProcessedDocument, item.decision_doc_id)
    assert doc is not None
    assert doc.financial_year == 2026
    assert doc.direction == Direction.expense
    assert doc.extraction is not None
    assert doc.extraction.amount_cents == 8900


def test_process_item_parks_when_proposing_new_scope():
    db, _scope, _cat, job = _setup()
    wizard.enqueue_items(db, job, FakeSource([_src("hoa.pdf")]))
    item = db.query(ImportItem).first()

    fields = InvoiceFields(
        text="HOA dues",
        counterparty="Mountain HOA",
        amount_cents=12000,
        currency="USD",
        doc_date=date(2025, 6, 1),
        due_date=None,
        account_number=None,
        raw_entities={},
    )
    llm = FakeLLM(
        LLMSuggestion(
            scope_name="Mountain Cabin",
            scope_kind_hint="property",
            category_name="HOA fees",
            direction=Direction.expense,
            counterparty="Mountain HOA",
            confidence=0.6,
            reasoning="new property",
            new_scope=True,
            new_category=True,
        )
    )
    uploader = FakeUploader()
    status = wizard.process_item(
        db,
        item,
        fetch_bytes=lambda _i: b"X",
        extractor=FakeExtractor(fields),
        llm=llm,
        uploader=uploader,
    )
    assert status == ImportItemStatus.awaiting
    assert uploader.uploads == []
    db.refresh(item)
    assert item.proposed_scope_name == "Mountain Cabin"
    assert item.proposed_scope_kind == ScopeKind.property
    assert item.proposed_category_name == "HOA fees"
    assert item.confidence == 0.6
    assert job.awaiting_count == 1


def test_process_item_skips_duplicates():
    db, scope, cat, job = _setup()
    # Pre-existing filed doc with the same hash
    from app.utils.hashing import sha256_bytes
    digest = sha256_bytes(b"PAYLOAD")
    db.add(
        ProcessedDocument(
            sha256=digest,
            original_filename="prev.pdf",
            classifier="manual",
            status="filed",
            scope_id=scope.id,
            category_id=cat.id,
            direction="expense",
        )
    )
    db.commit()

    wizard.enqueue_items(db, job, FakeSource([_src("dup.pdf")]))
    item = db.query(ImportItem).first()
    status = wizard.process_item(
        db,
        item,
        fetch_bytes=lambda _i: b"PAYLOAD",
        extractor=None,
        llm=None,
        uploader=None,
    )
    assert status == ImportItemStatus.skipped
    assert job.skipped_count == 1


def test_apply_decision_files_and_optionally_creates_rule():
    db, scope, cat, job = _setup()
    wizard.enqueue_items(db, job, FakeSource([_src("new.pdf")]))
    item = db.query(ImportItem).first()
    item.status = ImportItemStatus.awaiting
    item.ocr_text = "Some Vendor invoice"
    item.counterparty = "Some Vendor"
    item.extracted_doc_date = date(2025, 9, 1)
    job.awaiting_count = 1
    db.commit()

    uploader = FakeUploader()
    doc = wizard.apply_decision(
        db,
        item,
        fetch_bytes=lambda _i: b"X",
        uploader=uploader,
        scope_id=scope.id,
        category_id=cat.id,
        direction=Direction.expense,
        save_as_rule=True,
        rule_pattern="Some Vendor",
    )
    assert doc.scope_id == scope.id
    assert doc.financial_year == 2026
    db.refresh(item)
    assert item.status == ImportItemStatus.copied
    assert job.awaiting_count == 0
    assert job.copied_count == 1

    from app.models import Rule
    rules = db.query(Rule).all()
    assert len(rules) == 1
    assert rules[0].pattern == "Some Vendor"
    assert rules[0].scope_id == scope.id
