from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.classifier.llm import LLMSuggestion
from app.drive.fake_uploader import FakeDriveUploader
from app.extraction.invoice import InvoiceFields
from app.models import (
    Base,
    Category,
    Direction,
    DocStatus,
    MatchType,
    PdfPassword,
    ProcessedDocument,
    Rule,
    Scope,
    ScopeCountry,
    ScopeKind,
    Source,
    SourceKind,
)
from app.pipeline import process_raw_doc
from app.pollers.base import RawDoc


class FakeLLM:
    def __init__(self, suggestion: LLMSuggestion):
        self._s = suggestion

    def classify(self, **kwargs):
        return self._s


class StaticExtractor:
    def __init__(self, fields: InvoiceFields):
        self._f = fields

    def extract(self, *, data: bytes, mime_type: str) -> InvoiceFields:
        return self._f


def _setup() -> tuple[Session, Scope, Category, Source]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    scope = Scope(kind=ScopeKind.property, name="Beach House", country=ScopeCountry.AU)
    cat = Category(name="Electricity")
    source = Source(kind=SourceKind.dropbox, label="demo", enabled=True)
    db.add_all([scope, cat, source])
    db.commit()
    return db, scope, cat, source


def test_pipeline_files_via_rule_and_writes_to_fake_drive(tmp_path: Path):
    db, scope, cat, source = _setup()
    db.add(
        Rule(
            match_type=MatchType.contains,
            pattern="origin",
            scope_id=scope.id,
            category_id=cat.id,
            direction=Direction.expense,
            priority=10,
            confidence=0.95,
        )
    )
    db.commit()

    raw = RawDoc(
        data=b"PDF-with-origin-energy",
        filename="origin.pdf",
        source_msg_ref="origin.pdf",
        source_id=source.id,
        mime_type="application/pdf",
    )
    extractor = StaticExtractor(
        InvoiceFields(
            text="ORIGIN ENERGY",
            counterparty="Origin Energy",
            amount_cents=8900,
            currency="AUD",
            doc_date=None,
            due_date=None,
            account_number=None,
            raw_entities={},
        )
    )
    uploader = FakeDriveUploader(tmp_path)

    result = process_raw_doc(db, raw, extractor=extractor, llm=None, uploader=uploader)
    assert result.status == DocStatus.filed
    assert result.drive_path == "Properties/Beach House/Electricity/origin.pdf"

    target = tmp_path / "Properties" / "Beach House" / "Electricity" / "origin.pdf"
    assert target.exists()
    assert target.read_bytes() == b"PDF-with-origin-energy"


def test_pipeline_routes_to_unsorted_when_no_classifier_signal(tmp_path: Path):
    db, _scope, _cat, source = _setup()
    raw = RawDoc(
        data=b"random pdf",
        filename="weird.pdf",
        source_msg_ref="weird.pdf",
        source_id=source.id,
        mime_type="application/pdf",
    )
    extractor = StaticExtractor(
        InvoiceFields(
            text="random text",
            counterparty=None,
            amount_cents=None,
            currency=None,
            doc_date=None,
            due_date=None,
            account_number=None,
            raw_entities={},
        )
    )
    uploader = FakeDriveUploader(tmp_path)
    result = process_raw_doc(db, raw, extractor=extractor, llm=None, uploader=uploader)
    assert result.status == DocStatus.unsorted
    assert result.drive_path == "Unsorted/weird.pdf"
    assert (tmp_path / "Unsorted" / "weird.pdf").exists()


def test_pipeline_dedups_by_sha(tmp_path: Path):
    db, scope, cat, source = _setup()
    db.add(
        ProcessedDocument(
            sha256="DUPLICATE-PRE-RECORDED",
            original_filename="prev.pdf",
            classifier="manual",
            scope_id=scope.id,
            category_id=cat.id,
            direction=Direction.expense,
            status=DocStatus.filed,
        )
    )
    # Override sha256_bytes to return our marker for any input
    from app import pipeline as pipeline_module
    original = pipeline_module.sha256_bytes
    pipeline_module.sha256_bytes = lambda _data: "DUPLICATE-PRE-RECORDED"
    try:
        raw = RawDoc(
            data=b"anything",
            filename="dup.pdf",
            source_msg_ref="dup.pdf",
            source_id=source.id,
            mime_type="application/pdf",
        )
        extractor = StaticExtractor(
            InvoiceFields(
                text="x", counterparty=None, amount_cents=None, currency=None,
                doc_date=None, due_date=None, account_number=None, raw_entities={},
            )
        )
        result = process_raw_doc(
            db, raw, extractor=extractor, llm=None, uploader=FakeDriveUploader(tmp_path)
        )
    finally:
        pipeline_module.sha256_bytes = original
    assert "duplicate" in result.reason
    assert result.status == DocStatus.filed
