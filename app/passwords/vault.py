"""Try-and-decrypt helpers for password-protected PDFs.

The vault is small (a handful of bank/utility passwords). We rank candidates
by `priority` then prefer those whose `sender_match` or `filename_match`
substring matches the incoming document's metadata, so we don't iterate
through every password on every encrypted PDF.

Lazy-imports `pikepdf` so the rest of the app doesn't need it.
"""
from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PdfPassword

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecryptResult:
    data: bytes              # decrypted bytes (or original if not encrypted)
    used_password_id: int | None
    was_encrypted: bool


def is_encrypted(data: bytes) -> bool:
    """Cheap header check — does this PDF have an /Encrypt dict?"""
    if not data.startswith(b"%PDF"):
        return False
    # /Encrypt usually appears in the first ~16K of the trailer
    return b"/Encrypt" in data[:65536]


def try_decrypt(
    db: Session,
    *,
    data: bytes,
    sender: str | None = None,
    filename: str | None = None,
) -> DecryptResult:
    """Return decrypted bytes if `data` is an encrypted PDF and one of the
    stored passwords succeeds. If not encrypted, returns `data` unchanged."""

    if not is_encrypted(data):
        return DecryptResult(data=data, used_password_id=None, was_encrypted=False)

    candidates = _ranked_candidates(db, sender=sender, filename=filename)
    if not candidates:
        raise EncryptedPdfError("PDF is encrypted; vault has no passwords stored.")

    try:
        import pikepdf  # type: ignore
    except ImportError as exc:
        raise EncryptedPdfError(
            "PDF is encrypted; install pikepdf to decrypt."
        ) from exc

    for entry in candidates:
        try:
            with pikepdf.open(io.BytesIO(data), password=entry.password) as pdf:
                buffer = io.BytesIO()
                pdf.save(buffer)
                logger.info(
                    "Decrypted PDF using password '%s' (id=%d)", entry.label, entry.id
                )
                return DecryptResult(
                    data=buffer.getvalue(),
                    used_password_id=entry.id,
                    was_encrypted=True,
                )
        except pikepdf.PasswordError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("pikepdf failed for password '%s': %s", entry.label, exc)
            continue

    raise EncryptedPdfError(
        f"PDF is encrypted; none of {len(candidates)} stored passwords worked."
    )


def _ranked_candidates(
    db: Session, *, sender: str | None, filename: str | None
) -> list[PdfPassword]:
    rows: Sequence[PdfPassword] = list(
        db.scalars(select(PdfPassword).order_by(PdfPassword.priority.asc()))
    )
    sender_l = (sender or "").lower()
    filename_l = (filename or "").lower()

    def score(entry: PdfPassword) -> tuple[int, int, int]:
        sender_hit = bool(entry.sender_match) and entry.sender_match.lower() in sender_l
        filename_hit = (
            bool(entry.filename_match)
            and entry.filename_match.lower() in filename_l
        )
        # Lower tuple sorts first; matchers beat priority, then priority breaks ties
        return (0 if (sender_hit or filename_hit) else 1, entry.priority, entry.id)

    return sorted(rows, key=score)


class EncryptedPdfError(Exception):
    """Raised when decryption fails — message becomes processed_documents.error."""
