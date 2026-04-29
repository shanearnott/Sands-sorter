from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.config import get_settings
from app.models import DocumentExtraction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InvoiceFields:
    """Subset of Document AI Invoice Parser entities we keep."""

    text: str
    counterparty: str | None
    amount_cents: int | None
    currency: str | None
    doc_date: date | None
    due_date: date | None
    account_number: str | None
    raw_entities: dict


# --- entity helpers ---------------------------------------------------------

_ENTITY_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    m = _ENTITY_DATE.match(value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_amount_cents(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value)
    if not cleaned:
        return None
    try:
        return int((Decimal(cleaned) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


def fields_from_documentai(document) -> InvoiceFields:
    """Map a Document AI `Document` response into our compact representation.

    Tolerates absent entities — Document AI returns confidence-scored entities,
    not all of which fire on every invoice. Falls back to None for missing
    fields and lets the LLM/user fill the gap downstream.
    """
    raw: dict[str, str] = {}
    for entity in getattr(document, "entities", []) or []:
        type_name = getattr(entity, "type_", None) or getattr(entity, "type", None)
        if not type_name:
            continue
        # Prefer normalized_value when present (e.g. dates pre-parsed).
        normalized = getattr(entity, "normalized_value", None)
        text = getattr(normalized, "text", None) if normalized else None
        if not text:
            text = getattr(entity, "mention_text", None)
        if text:
            # Keep the highest-confidence value if a key repeats.
            raw.setdefault(type_name, text)

    return InvoiceFields(
        text=getattr(document, "text", "") or "",
        counterparty=raw.get("supplier_name") or raw.get("remit_to_name"),
        amount_cents=_parse_amount_cents(raw.get("total_amount")),
        currency=(raw.get("currency") or "").upper()[:3] or None,
        doc_date=_parse_date(raw.get("invoice_date")),
        due_date=_parse_date(raw.get("due_date")),
        account_number=raw.get("supplier_account_number") or raw.get("invoice_id"),
        raw_entities=raw,
    )


# --- Document AI client ----------------------------------------------------

class InvoiceExtractor:
    """Thin wrapper around `documentai.DocumentProcessorServiceClient`. The
    client is lazily constructed so unit tests can use `fields_from_documentai`
    against canned responses without needing GCP credentials."""

    def __init__(self, client=None, processor_name: str | None = None):
        self._client = client
        self._processor_name = processor_name

    def _ensure(self) -> tuple[object, str]:
        if self._client is not None and self._processor_name is not None:
            return self._client, self._processor_name

        # Imported lazily so the rest of the app doesn't pull GCP at import time.
        from google.cloud import documentai  # type: ignore

        settings = get_settings()
        if not (settings.gcp_project_id and settings.document_ai_processor_id):
            raise RuntimeError("Document AI is not configured (GCP_PROJECT_ID / DOCUMENT_AI_PROCESSOR_ID).")

        client = documentai.DocumentProcessorServiceClient()
        name = client.processor_path(
            settings.gcp_project_id,
            settings.document_ai_location,
            settings.document_ai_processor_id,
        )
        self._client = client
        self._processor_name = name
        return client, name

    def extract(self, *, data: bytes, mime_type: str) -> InvoiceFields:
        from google.cloud import documentai  # type: ignore

        client, name = self._ensure()
        raw_doc = documentai.RawDocument(content=data, mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, raw_document=raw_doc)
        result = client.process_document(request=request)
        return fields_from_documentai(result.document)


def persist_extraction(
    document_id: int, fields: InvoiceFields
) -> DocumentExtraction:
    """Build a `DocumentExtraction` row (caller `add()`s + commits)."""
    return DocumentExtraction(
        document_id=document_id,
        counterparty_text=fields.counterparty,
        amount_cents=fields.amount_cents,
        currency=fields.currency,
        doc_date=fields.doc_date,
        due_date=fields.due_date,
        account_number=fields.account_number,
        raw_json=fields.raw_entities or None,
    )
