"""Demo Gmail poller. Reads RFC-822 `.eml` files from a local inbox folder
and yields a `RawDoc` per attachment. Files are moved into `processed/`
once the pipeline acks them. Useful for end-to-end feature testing
without Gmail OAuth.
"""
from __future__ import annotations

import email
import logging
import mimetypes
import shutil
from collections.abc import Iterable
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Source
from app.pollers.base import RawDoc

logger = logging.getLogger(__name__)


class LocalMailboxPoller:
    """Walks `<inbox_root>/inbox/*.eml`. Anything moved to
    `<inbox_root>/processed/<YYYY-MM>/` after `mark_processed()`."""

    def __init__(self, db: Session, source: Source, inbox_root: str | Path):
        self._db = db
        self._source = source
        self._root = Path(inbox_root).resolve()
        self._inbox = self._root / "inbox"
        self._processed = self._root / "processed"
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._processed.mkdir(parents=True, exist_ok=True)

    def fetch(self) -> Iterable[RawDoc]:
        for eml_path in sorted(self._inbox.glob("*.eml")):
            try:
                yield from self._yield_from(eml_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("eml %s skipped: %s", eml_path, exc)
        self._record_success()

    def _yield_from(self, eml_path: Path) -> Iterable[RawDoc]:
        msg: EmailMessage = email.message_from_bytes(
            eml_path.read_bytes(), policy=policy.default
        )
        sender = (msg.get("from") or "").strip() or None
        received = _parse_date(msg.get("date"))
        ref = eml_path.name

        attachments = [
            part for part in msg.iter_attachments() if part.get_filename()
        ]
        if attachments:
            for part in attachments:
                payload = part.get_payload(decode=True) or b""
                yield RawDoc(
                    data=payload,
                    filename=part.get_filename() or f"{ref}.bin",
                    source_msg_ref=ref,
                    source_id=self._source.id,
                    mime_type=part.get_content_type() or _guess_mime(part.get_filename()),
                    sender=sender,
                    received_at=received,
                )
            return

        # Body-allowlist path — only emit if the sender is allowlisted.
        if not _sender_allowed(sender, self._source.body_allowlist):
            logger.debug("eml %s has no attachments and sender not allowlisted; skipping", ref)
            return
        body_html = _body_html(msg) or _body_text(msg) or ""
        yield RawDoc(
            data=body_html.encode("utf-8"),
            filename=f"{eml_path.stem}.html",
            source_msg_ref=ref,
            source_id=self._source.id,
            mime_type="text/html",
            sender=sender,
            received_at=received,
            extra={"body_only": True},
        )

    def mark_processed(self, raw: RawDoc) -> None:
        src = self._inbox / raw.source_msg_ref
        if not src.exists():
            return  # already moved by a previous attachment from the same .eml
        bucket = (raw.received_at or datetime.now(tz=timezone.utc)).strftime("%Y-%m")
        dest_dir = self._processed / bucket
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dest_dir / src.name))
        except OSError as exc:
            logger.warning("Could not move %s: %s", src, exc)

    def _record_success(self) -> None:
        self._source.last_polled_at = datetime.now(tz=timezone.utc)
        self._source.last_error = None
        self._db.commit()


def _body_html(msg: EmailMessage) -> str | None:
    part = msg.get_body(preferencelist=("html",))
    if part is None:
        return None
    payload = part.get_content()
    return payload if isinstance(payload, str) else payload.decode("utf-8", "replace")


def _body_text(msg: EmailMessage) -> str | None:
    part = msg.get_body(preferencelist=("plain",))
    if part is None:
        return None
    payload = part.get_content()
    return payload if isinstance(payload, str) else payload.decode("utf-8", "replace")


def _sender_allowed(sender: str | None, allowlist: list[str] | None) -> bool:
    if not sender or not allowlist:
        return False
    sender_lower = sender.lower()
    return any(needle.lower() in sender_lower for needle in allowlist if needle)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _guess_mime(name: str | None) -> str:
    if not name:
        return "application/octet-stream"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
