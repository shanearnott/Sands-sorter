"""Normalise HEIC frames (iPhone screenshots since iOS 11) to JPEG so
Document AI / pdfplumber / pytesseract can read them. Lazy-imports
`pillow-heif` and registers it with PIL on first call.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

_REGISTERED = False


def is_heic(mime_type: str | None, filename: str | None) -> bool:
    if mime_type:
        m = mime_type.lower()
        if m in {"image/heic", "image/heif"}:
            return True
    if filename:
        suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        return suffix in {"heic", "heif"}
    return False


def heic_to_jpeg(data: bytes, *, quality: int = 92) -> tuple[bytes, str]:
    """Decode HEIC/HEIF bytes and return (`jpeg_bytes`, `"image/jpeg"`)."""
    global _REGISTERED
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Pillow not installed") from exc

    if not _REGISTERED:
        try:
            from pillow_heif import register_heif_opener  # type: ignore

            register_heif_opener()
            _REGISTERED = True
        except ImportError as exc:
            raise RuntimeError(
                "pillow-heif not installed; cannot decode HEIC"
            ) from exc

    image = Image.open(io.BytesIO(data))
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    logger.info(
        "Converted HEIC -> JPEG (%d bytes -> %d bytes)", len(data), buffer.tell()
    )
    return buffer.getvalue(), "image/jpeg"
