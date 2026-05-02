"""Best-effort local OCR/extraction so demo mode runs without Document AI.

Uses `pdfplumber` for PDFs (text extraction only — no structured-field
parsing) and `pytesseract` for images. Both are lazy-imported so the rest
of the app doesn't pay for them when Document AI is configured.

Returns the same `InvoiceFields` dataclass so callers don't branch on the
extractor implementation. Most structured fields stay None — the LLM
classifier picks up the slack from the OCR text.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date

from app.extraction.invoice import InvoiceFields, _parse_amount_cents, _parse_date

logger = logging.getLogger(__name__)


_AMOUNT_RE = re.compile(
    r"(?:total|amount\s*due|balance\s*due)\s*[:$]?\s*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


class FallbackExtractor:
    """Drop-in for `InvoiceExtractor` that uses local libraries."""

    def extract(self, *, data: bytes, mime_type: str) -> InvoiceFields:
        if mime_type == "application/pdf" or mime_type.endswith("pdf"):
            text = _extract_pdf_text(data)
        elif mime_type.startswith("image/"):
            text = _extract_image_text(data)
        elif mime_type in {"text/html", "text/plain"}:
            text = data.decode("utf-8", errors="replace")
        else:
            text = ""

        return InvoiceFields(
            text=text,
            counterparty=_guess_counterparty(text),
            amount_cents=_guess_amount_cents(text),
            currency=_guess_currency(text),
            doc_date=_guess_date(text),
            due_date=None,
            account_number=None,
            raw_entities={},
        )


def _extract_pdf_text(data: bytes) -> str:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        logger.warning("pdfplumber not installed; falling back to empty OCR text")
        return ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(
                (page.extract_text() or "") for page in pdf.pages
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdfplumber failed: %s", exc)
        return ""


def _extract_image_text(data: bytes) -> str:
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
    except ImportError:
        logger.warning("pytesseract/Pillow not installed; falling back to empty OCR text")
        return ""
    try:
        image = Image.open(io.BytesIO(data))
        return pytesseract.image_to_string(image)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pytesseract failed: %s", exc)
        return ""


def _guess_counterparty(text: str) -> str | None:
    if not text:
        return None
    # Heuristic: first non-empty line is often the supplier name.
    for line in text.splitlines():
        clean = line.strip()
        if clean and len(clean) >= 3 and len(clean) <= 80:
            return clean
    return None


def _guess_amount_cents(text: str) -> int | None:
    if not text:
        return None
    match = _AMOUNT_RE.search(text)
    if match:
        return _parse_amount_cents(match.group(1))
    return None


def _guess_currency(text: str) -> str | None:
    if not text:
        return None
    if re.search(r"\bAUD\b|\bA\$", text):
        return "AUD"
    if re.search(r"\bUSD\b|US\$", text):
        return "USD"
    if "$" in text:
        return None  # ambiguous
    return None


def _guess_date(text: str) -> date | None:
    if not text:
        return None
    match = _DATE_RE.search(text)
    if match:
        return _parse_date(match.group(1))
    return None
