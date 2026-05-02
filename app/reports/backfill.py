"""One-shot job: re-OCR docs that were filed before extraction was wired
(or whose extraction row is missing). Runs via
`python -m app.worker backfill-extractions`.

Skips docs without `drive_file_id` (they live in fake/demo Drive only)
unless `DEMO_DRIVE_ROOT` is set, in which case it reads from disk.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.extraction.invoice import persist_extraction
from app.models import (
    DocStatus,
    DocumentExtraction,
    ProcessedDocument,
)

logger = logging.getLogger(__name__)


def backfill_extractions(db: Session) -> int:
    settings = get_settings()
    extractor = _build_extractor(settings)
    if extractor is None:
        logger.info("No extractor available; backfill cannot run.")
        return 0

    missing = list(
        db.scalars(
            select(ProcessedDocument)
            .where(ProcessedDocument.status == DocStatus.filed)
            .where(
                ~select(DocumentExtraction.id)
                .where(DocumentExtraction.document_id == ProcessedDocument.id)
                .exists()
            )
        )
    )

    count = 0
    for doc in missing:
        data = _read_doc_bytes(doc, settings)
        if data is None:
            continue
        try:
            fields = extractor.extract(
                data=data, mime_type="application/pdf"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Backfill OCR failed for doc %s: %s", doc.id, exc)
            continue
        db.add(persist_extraction(doc.id, fields))
        count += 1
    if count:
        db.commit()
    return count


def _build_extractor(settings):
    if settings.demo_ocr or not (
        settings.gcp_project_id and settings.document_ai_processor_id
    ):
        from app.extraction.fallback import FallbackExtractor

        return FallbackExtractor()
    try:
        from app.extraction.invoice import InvoiceExtractor

        return InvoiceExtractor()
    except Exception:  # noqa: BLE001
        from app.extraction.fallback import FallbackExtractor

        return FallbackExtractor()


def _read_doc_bytes(doc: ProcessedDocument, settings) -> bytes | None:
    """For demo Drive, drive_path is relative to demo_drive_root and we can
    read directly. For live Drive, we'd need to call the Drive API — out of
    scope for the M5 backfill stub."""
    if not doc.drive_path:
        return None
    if settings.demo_drive_root:
        path = Path(settings.demo_drive_root) / doc.drive_path
        if path.exists():
            try:
                return path.read_bytes()
            except OSError as exc:
                logger.warning("Could not read %s: %s", path, exc)
    return None
