"""Importer wizard orchestration.

Two phases:
1. `enqueue_items(job, source)` — walk the tree, persist `ImportItem` rows
   with status=pending. Cheap and doesn't call OCR/LLM.
2. `process_item(item, ...)` — for one pending item: hash, dedup, OCR,
   classify, then either auto-file (high confidence) or park as `awaiting`.

The wizard is split into pure functions so the web layer can pass them to a
background task or call them sequentially.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier.llm import ClaudeFallback
from app.classifier.pipeline import classify
from app.config import get_settings
from app.drive.paths import build_path
from app.drive.uploader import DriveUploader
from app.extraction.invoice import (
    InvoiceExtractor,
    InvoiceFields,
    persist_extraction,
)
from app.importer.sources import SourceItem, TreeSource, folder_segments
from app.models import (
    Category,
    Classifier,
    DocStatus,
    DocumentExtraction,
    ImportItem,
    ImportItemStatus,
    ImportJob,
    ImportJobStatus,
    ProcessedDocument,
    Scope,
    ScopeKind,
)
from app.utils.fy import financial_year
from app.utils.hashing import sha256_bytes

logger = logging.getLogger(__name__)


# --- Phase 1 — enqueue ------------------------------------------------------

def enqueue_items(db: Session, job: ImportJob, source: TreeSource) -> int:
    """Walk the source tree, insert pending ImportItem rows. Returns count.

    Idempotent on `(job_id, source_path)` — re-running on the same job appends
    only new files seen on disk/Drive (handy when an import is paused).
    """
    seen = {
        row[0]
        for row in db.execute(
            select(ImportItem.source_path).where(ImportItem.job_id == job.id)
        )
    }
    added = 0
    for item in source.iter_items():
        if item.relative_path in seen:
            continue
        db.add(_item_from_source(job.id, item))
        added += 1
    if added:
        job.total_count = (job.total_count or 0) + added
    db.commit()
    return added


def _item_from_source(job_id: int, source: SourceItem) -> ImportItem:
    return ImportItem(
        job_id=job_id,
        source_path=source.relative_path,
        source_file_id=getattr(source, "file_id", None),
        mime_type=source.mime_type,
        filename=source.filename,
        status=ImportItemStatus.pending,
    )


# --- Phase 2 — classify one item -------------------------------------------

def process_item(
    db: Session,
    item: ImportItem,
    *,
    fetch_bytes,
    extractor: InvoiceExtractor | None,
    llm: ClaudeFallback | None,
    uploader: DriveUploader | None,
) -> ImportItemStatus:
    """Classify one pending item. Auto-files when classification is fully
    resolved and confidence clears the threshold, otherwise parks as
    `awaiting` for the user to decide."""

    settings = get_settings()
    job = db.get(ImportJob, item.job_id)
    if job is None:
        raise RuntimeError("ImportItem references a missing job")

    try:
        data = fetch_bytes(item)
    except Exception as exc:  # noqa: BLE001 - we want to capture and persist any error
        return _mark_error(db, job, item, f"could not fetch bytes: {exc}")

    digest = sha256_bytes(data)
    item.sha256 = digest

    existing = db.scalar(
        select(ProcessedDocument).where(ProcessedDocument.sha256 == digest)
    )
    if existing:
        item.status = ImportItemStatus.skipped
        item.decision_doc_id = existing.id
        job.skipped_count += 1
        db.commit()
        return item.status

    fields: InvoiceFields | None = None
    if extractor is not None:
        try:
            fields = extractor.extract(
                data=data, mime_type=item.mime_type or "application/octet-stream"
            )
            item.ocr_text = fields.text[:50_000] if fields.text else None
            item.extracted_amount_cents = fields.amount_cents
            item.extracted_currency = fields.currency
            item.extracted_doc_date = fields.doc_date
            item.extracted_due_date = fields.due_date
            item.counterparty = fields.counterparty
        except Exception as exc:  # noqa: BLE001
            return _mark_error(db, job, item, f"OCR failed: {exc}")

    classification = classify(
        db,
        filename=item.filename,
        sender_email=None,
        ocr_text=(fields.text if fields else None),
        llm=llm,
    )

    item.proposed_scope_id = classification.scope_id
    item.proposed_scope_name = classification.scope_proposal_name
    item.proposed_scope_kind = classification.scope_proposal_kind
    item.proposed_category_id = classification.category_id
    item.proposed_category_name = classification.category_proposal_name
    item.proposed_direction = classification.direction
    item.confidence = classification.confidence
    item.llm_reasoning = classification.reasoning
    if not item.counterparty:
        item.counterparty = classification.counterparty

    threshold = settings.confidence_threshold
    fully_resolved = (
        classification.scope_id is not None
        and classification.category_id is not None
        and classification.scope_proposal_name is None
        and classification.category_proposal_name is None
    )
    # Dry-run jobs never auto-file: every item lands in the awaiting queue
    # so the user can review the proposed Drive path before any upload.
    auto_ok = (
        fully_resolved and classification.confidence >= threshold and not job.dry_run
    )

    # Compute the proposed Drive path + FY using the resolved scope (if any).
    scope = (
        db.get(Scope, classification.scope_id)
        if classification.scope_id is not None
        else None
    )
    effective_date = (fields.doc_date if fields else None) or _modified_or_today(item)
    proposed_path, fy = build_path(
        scope_kind=scope.kind if scope else None,
        scope_name=scope.name if scope else None,
        scope_country=scope.country if scope else None,
        category_name=(_category_name(db, classification.category_id) if scope else None),
        doc_date=effective_date,
        filename=item.filename,
    )
    item.proposed_fy = fy
    item.proposed_drive_path = str(proposed_path)

    if not auto_ok or uploader is None:
        item.status = ImportItemStatus.awaiting
        job.awaiting_count += 1
        db.commit()
        return item.status

    # Auto-file
    upload = uploader.upload(path=proposed_path, data=data, mime_type=item.mime_type)
    doc = ProcessedDocument(
        sha256=digest,
        original_filename=item.filename,
        ocr_text=(fields.text if fields else None),
        classifier=classification.classifier,
        rule_id=classification.rule_id,
        scope_id=classification.scope_id,
        category_id=classification.category_id,
        direction=classification.direction,
        doc_date=effective_date,
        financial_year=fy,
        drive_file_id=upload.file_id,
        drive_path=upload.drive_path,
        confidence=classification.confidence,
        status=DocStatus.filed,
        filed_at=datetime.utcnow(),
    )
    db.add(doc)
    db.flush()  # need doc.id before linking the extraction row
    if fields is not None:
        db.add(persist_extraction(doc.id, fields))

    item.status = ImportItemStatus.copied
    item.decision_doc_id = doc.id
    job.copied_count += 1
    db.commit()
    return item.status


def _mark_error(
    db: Session, job: ImportJob, item: ImportItem, message: str
) -> ImportItemStatus:
    logger.warning("import item %s -> error: %s", item.id, message)
    item.status = ImportItemStatus.error
    item.error = message
    job.error_count += 1
    db.commit()
    return item.status


def _modified_or_today(item: ImportItem) -> date:
    # We didn't persist the source mtime onto the item; fall back to today.
    # In practice doc_date almost always lands via Document AI extraction.
    return date.today()


def _category_name(db: Session, category_id: int | None) -> str | None:
    if category_id is None:
        return None
    cat = db.get(Category, category_id)
    return cat.name if cat else None


# --- Decisions (Phase 3, applied via the web UI) ----------------------------

def apply_decision(
    db: Session,
    item: ImportItem,
    *,
    fetch_bytes,
    uploader: DriveUploader,
    scope_id: int,
    category_id: int,
    direction,
    save_as_rule: bool,
    rule_pattern: str | None = None,
) -> ProcessedDocument:
    """Commit the user's choice for an awaiting item: file it, optionally
    teach the rules engine, mark the item copied."""
    from app.models import MatchType, Rule, RuleSource

    job = db.get(ImportJob, item.job_id)
    scope = db.get(Scope, scope_id)
    category = db.get(Category, category_id)
    if not (job and scope and category):
        raise RuntimeError("Decision references missing job / scope / category")

    data = fetch_bytes(item)
    digest = item.sha256 or sha256_bytes(data)

    effective_date = item.extracted_doc_date or date.today()
    path, fy = build_path(
        scope_kind=scope.kind,
        scope_name=scope.name,
        scope_country=scope.country,
        category_name=category.name,
        doc_date=effective_date,
        filename=item.filename,
    )
    upload = uploader.upload(path=path, data=data, mime_type=item.mime_type)

    doc = ProcessedDocument(
        sha256=digest,
        original_filename=item.filename,
        ocr_text=item.ocr_text,
        classifier=Classifier.manual,
        scope_id=scope.id,
        category_id=category.id,
        direction=direction,
        doc_date=effective_date,
        financial_year=fy,
        drive_file_id=upload.file_id,
        drive_path=upload.drive_path,
        confidence=1.0,
        status=DocStatus.filed,
        filed_at=datetime.utcnow(),
    )
    db.add(doc)
    db.flush()
    if item.extracted_amount_cents is not None or item.counterparty:
        db.add(
            DocumentExtraction(
                document_id=doc.id,
                counterparty_text=item.counterparty,
                amount_cents=item.extracted_amount_cents,
                currency=item.extracted_currency,
                doc_date=item.extracted_doc_date,
                due_date=item.extracted_due_date,
            )
        )

    if save_as_rule and rule_pattern:
        db.add(
            Rule(
                match_type=MatchType.contains,
                pattern=rule_pattern,
                scope_id=scope.id,
                category_id=category.id,
                direction=direction,
                priority=50,                  # learned rules sit ahead of defaults
                confidence=0.9,
                source=RuleSource.learned_from_reassign,
            )
        )

    item.status = ImportItemStatus.copied
    item.decision_doc_id = doc.id
    item.proposed_scope_id = scope.id
    item.proposed_category_id = category.id
    item.proposed_direction = direction
    item.proposed_drive_path = str(path)
    item.proposed_fy = fy

    if job.awaiting_count > 0:
        job.awaiting_count -= 1
    job.copied_count += 1

    db.commit()
    return doc


# --- Job lifecycle ----------------------------------------------------------

def mark_done_if_drained(db: Session, job: ImportJob) -> None:
    pending = db.scalar(
        select(ImportItem.id)
        .where(ImportItem.job_id == job.id)
        .where(ImportItem.status.in_([ImportItemStatus.pending, ImportItemStatus.awaiting]))
        .limit(1)
    )
    if pending is None and job.status not in (
        ImportJobStatus.done,
        ImportJobStatus.cancelled,
    ):
        job.status = ImportJobStatus.done
        job.finished_at = datetime.utcnow()
        db.commit()
