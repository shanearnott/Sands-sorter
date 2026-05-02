"""Shared pipeline used by both `/import` (interactive wizard) and the live
pollers in `/internal/poll`. Given a `RawDoc`, runs:

  1. PDF password vault (decrypt if encrypted)
  2. HEIC normalisation (HEIC → JPEG)
  3. Body-PDF rendering (text/html → PDF) for body-allowlisted senders
  4. SHA256 dedup
  5. OCR + invoice extraction (Document AI in live, fallback locally)
  6. Classifier (rules → LLM → Unsorted) — auto-files when fully resolved
     and confidence clears the threshold
  7. Drive upload (real or fake, based on credential availability)
  8. ProcessedDocument + DocumentExtraction rows; source cleanup ack

Returns a `PipelineResult` summary the caller can roll into stats.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier.llm import ClaudeFallback
from app.classifier.pipeline import classify
from app.config import get_settings
from app.drive.paths import build_path
from app.email_render import body_pdf as body_pdf_module
from app.email_render.heic import heic_to_jpeg, is_heic
from app.extraction.invoice import InvoiceExtractor, persist_extraction
from app.extraction.fallback import FallbackExtractor
from app.passwords.vault import EncryptedPdfError, try_decrypt
from app.models import (
    Category,
    Classifier,
    DocStatus,
    ProcessedDocument,
    Scope,
)
from app.pollers.base import RawDoc
from app.utils.hashing import sha256_bytes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    status: DocStatus
    doc_id: int | None
    drive_path: str | None
    reason: str  # human-readable "filed to ...", "duplicate", "encrypted: ..."


def process_raw_doc(
    db: Session,
    raw: RawDoc,
    *,
    extractor=None,                     # InvoiceExtractor or FallbackExtractor
    llm: ClaudeFallback | None = None,
    uploader=None,                      # DriveUploader or FakeDriveUploader
) -> PipelineResult:
    settings = get_settings()
    data = raw.data
    mime = raw.mime_type
    filename = raw.filename

    # 1. Password vault (PDF only — is_encrypted has its own short-circuit)
    try:
        decrypted = try_decrypt(
            db, data=data, sender=raw.sender, filename=filename
        )
    except EncryptedPdfError as exc:
        return _record_failure(db, raw, reason=f"encrypted: {exc}")
    data = decrypted.data

    # 2. HEIC -> JPEG
    if is_heic(mime, filename):
        try:
            data, mime = heic_to_jpeg(data)
            if filename.lower().endswith((".heic", ".heif")):
                filename = filename.rsplit(".", 1)[0] + ".jpg"
        except RuntimeError as exc:
            return _record_failure(db, raw, reason=f"heic decode failed: {exc}")

    # 3. Body-PDF rendering
    if raw.extra.get("body_only") and mime in ("text/html", "text/plain"):
        try:
            data, mime = body_pdf_module.render(
                body=data.decode("utf-8", errors="replace"),
                is_html=mime == "text/html",
                sender=raw.sender,
                received=raw.received_at.isoformat() if raw.received_at else None,
            )
            if not filename.lower().endswith(".pdf"):
                filename = filename.rsplit(".", 1)[0] + ".pdf"
        except RuntimeError as exc:
            return _record_failure(db, raw, reason=f"body render failed: {exc}")

    # 4. Dedup
    digest = sha256_bytes(data)
    existing = db.scalar(
        select(ProcessedDocument).where(ProcessedDocument.sha256 == digest)
    )
    if existing:
        return PipelineResult(
            status=DocStatus.filed,
            doc_id=existing.id,
            drive_path=existing.drive_path,
            reason=f"duplicate of doc #{existing.id}",
        )

    # 5. OCR / extraction
    if extractor is None:
        extractor = _build_extractor()
    try:
        fields = extractor.extract(data=data, mime_type=mime)
    except Exception as exc:  # noqa: BLE001
        return _record_failure(db, raw, reason=f"OCR failed: {exc}")

    # 6. Classify
    classification = classify(
        db,
        filename=filename,
        sender_email=raw.sender,
        ocr_text=fields.text,
        llm=llm,
    )

    fully_resolved = (
        classification.scope_id is not None
        and classification.category_id is not None
        and classification.scope_proposal_name is None
        and classification.category_proposal_name is None
    )
    auto_ok = fully_resolved and classification.confidence >= settings.confidence_threshold

    scope = (
        db.get(Scope, classification.scope_id)
        if classification.scope_id is not None
        else None
    )
    effective_date = fields.doc_date or _date_from(raw)
    proposed_path, fy = build_path(
        scope_kind=scope.kind if scope else None,
        scope_name=scope.name if scope else None,
        scope_country=scope.country if scope else None,
        category_name=_category_name(db, classification.category_id) if scope else None,
        doc_date=effective_date,
        filename=filename,
    )

    # 7. Upload + persist
    if not auto_ok:
        return _file_unsorted(
            db, raw, digest, fields, filename, mime, classification, fy, uploader,
            reason="low confidence or unresolved scope/category",
        )

    if uploader is None:
        uploader = _build_uploader()

    upload = uploader.upload(path=proposed_path, data=data, mime_type=mime)
    doc = ProcessedDocument(
        sha256=digest,
        source_id=raw.source_id,
        source_msg_ref=raw.source_msg_ref,
        original_filename=filename,
        ocr_text=fields.text[:50_000] if fields.text else None,
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
    db.flush()
    db.add(persist_extraction(doc.id, fields))
    db.commit()
    return PipelineResult(
        status=DocStatus.filed,
        doc_id=doc.id,
        drive_path=upload.drive_path,
        reason=f"filed via {classification.classifier.value}",
    )


# --- helpers ----------------------------------------------------------------

def _file_unsorted(
    db, raw, digest, fields, filename, mime, classification, fy, uploader, *, reason
):
    if uploader is None:
        uploader = _build_uploader()
    proposed_path, _fy = build_path(
        scope_kind=None,
        scope_name=None,
        scope_country=None,
        category_name=None,
        doc_date=fields.doc_date,
        filename=filename,
    )
    upload = uploader.upload(path=proposed_path, data=raw.data, mime_type=mime)
    doc = ProcessedDocument(
        sha256=digest,
        source_id=raw.source_id,
        source_msg_ref=raw.source_msg_ref,
        original_filename=filename,
        ocr_text=fields.text[:50_000] if fields.text else None,
        classifier=Classifier.unsorted,
        scope_id=None,
        category_id=None,
        direction=classification.direction,
        doc_date=fields.doc_date,
        financial_year=fy,
        drive_file_id=upload.file_id,
        drive_path=upload.drive_path,
        confidence=classification.confidence,
        status=DocStatus.unsorted,
        filed_at=datetime.utcnow(),
    )
    db.add(doc)
    db.flush()
    if any([fields.amount_cents, fields.counterparty, fields.doc_date]):
        db.add(persist_extraction(doc.id, fields))
    db.commit()
    return PipelineResult(
        status=DocStatus.unsorted,
        doc_id=doc.id,
        drive_path=upload.drive_path,
        reason=reason,
    )


def _record_failure(db: Session, raw: RawDoc, *, reason: str) -> PipelineResult:
    """For pre-pipeline failures (decryption, HEIC, body rendering). Doesn't
    write to Drive — just records an error row so the user sees it in the
    daily summary."""
    digest = sha256_bytes(raw.data)
    doc = ProcessedDocument(
        sha256=digest,
        source_id=raw.source_id,
        source_msg_ref=raw.source_msg_ref,
        original_filename=raw.filename,
        classifier=Classifier.unsorted,
        status=DocStatus.error,
        error=reason,
    )
    db.add(doc)
    db.commit()
    logger.warning("Pipeline failure: %s — %s", raw.filename, reason)
    return PipelineResult(
        status=DocStatus.error, doc_id=doc.id, drive_path=None, reason=reason
    )


def _build_extractor():
    settings = get_settings()
    if settings.demo_ocr or not (
        settings.gcp_project_id and settings.document_ai_processor_id
    ):
        return FallbackExtractor()
    try:
        return InvoiceExtractor()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Document AI unavailable, using fallback OCR: %s", exc)
        return FallbackExtractor()


def _build_uploader():
    settings = get_settings()
    if settings.drive_root_folder_id and settings.drive_oauth_token_json:
        from app.drive.credentials import load_drive_credentials
        from app.drive.uploader import DriveUploader

        return DriveUploader(load_drive_credentials(), settings.drive_root_folder_id)
    if not settings.demo_drive_root:
        raise RuntimeError(
            "No Drive credentials and no DEMO_DRIVE_ROOT — cannot upload."
        )
    from app.drive.fake_uploader import FakeDriveUploader

    return FakeDriveUploader(settings.demo_drive_root)


def _category_name(db: Session, category_id: int | None) -> str | None:
    if category_id is None:
        return None
    cat = db.get(Category, category_id)
    return cat.name if cat else None


def _date_from(raw: RawDoc) -> date | None:
    if raw.received_at is None:
        return None
    return raw.received_at.date() if hasattr(raw.received_at, "date") else None
