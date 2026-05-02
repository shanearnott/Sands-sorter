"""Render an email body (HTML or plain text) to PDF so the live + demo
pollers can hand it to the same OCR/classifier pipeline as attachments.

Uses WeasyPrint when available; falls back to a tiny HTML-to-PDF wrapper
via `xhtml2pdf` if WeasyPrint isn't installed (saves cairo/Pango deps in
demo environments). Either way returns `(bytes, "application/pdf")`.
"""
from __future__ import annotations

import io
import logging
import re

logger = logging.getLogger(__name__)

_DEFAULT_CSS = """
@page { size: A4; margin: 18mm 14mm 14mm 14mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
       font-size: 11pt; line-height: 1.4; color: #111; }
h1, h2, h3 { color: #222; }
table { border-collapse: collapse; }
td, th { padding: 4px 8px; }
"""


def render(
    *,
    body: str,
    is_html: bool,
    subject: str | None = None,
    sender: str | None = None,
    received: str | None = None,
) -> tuple[bytes, str]:
    """Render a single email body to PDF bytes."""
    html_body = body if is_html else _text_to_html(body)
    full_html = _wrap_with_header(
        html_body, subject=subject, sender=sender, received=received
    )

    pdf = _try_weasyprint(full_html)
    if pdf is not None:
        return pdf, "application/pdf"

    pdf = _try_xhtml2pdf(full_html)
    if pdf is not None:
        return pdf, "application/pdf"

    raise RuntimeError(
        "Neither WeasyPrint nor xhtml2pdf is installed; cannot render body to PDF."
    )


def _try_weasyprint(html: str) -> bytes | None:
    try:
        from weasyprint import CSS, HTML  # type: ignore
    except (ImportError, OSError):
        return None
    try:
        return HTML(string=html).write_pdf(stylesheets=[CSS(string=_DEFAULT_CSS)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("WeasyPrint failed: %s", exc)
        return None


def _try_xhtml2pdf(html: str) -> bytes | None:
    try:
        from xhtml2pdf import pisa  # type: ignore
    except ImportError:
        return None
    try:
        buffer = io.BytesIO()
        pisa.CreatePDF(html, dest=buffer)
        return buffer.getvalue() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("xhtml2pdf failed: %s", exc)
        return None


def _text_to_html(body: str) -> str:
    escaped = (
        body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"<pre>{escaped}</pre>"


def _wrap_with_header(
    html_body: str,
    *,
    subject: str | None,
    sender: str | None,
    received: str | None,
) -> str:
    header_rows = []
    if subject:
        header_rows.append(f"<tr><th align='left'>Subject</th><td>{_esc(subject)}</td></tr>")
    if sender:
        header_rows.append(f"<tr><th align='left'>From</th><td>{_esc(sender)}</td></tr>")
    if received:
        header_rows.append(f"<tr><th align='left'>Received</th><td>{_esc(received)}</td></tr>")
    header_table = ""
    if header_rows:
        header_table = (
            "<table style='border:1px solid #ddd; margin-bottom:16px;'>"
            + "".join(header_rows)
            + "</table>"
        )

    # Strip any external resources to keep WeasyPrint offline-friendly.
    cleaned = _strip_external_resources(html_body)

    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><style>{_DEFAULT_CSS}</style></head>
<body>{header_table}{cleaned}</body></html>"""


_REMOTE_RE = re.compile(r'(href|src)="https?://[^"]*"', flags=re.IGNORECASE)
_TRACKING_PIXEL = re.compile(r"<img[^>]+(width=\"?1\"?|height=\"?1\"?)[^>]*>", re.IGNORECASE)


def _strip_external_resources(html: str) -> str:
    html = _TRACKING_PIXEL.sub("", html)
    html = _REMOTE_RE.sub(r'\1="#"', html)
    return html


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
