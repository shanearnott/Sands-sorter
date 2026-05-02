"""Live Gmail poller. Walks attachments and (for body-allowlisted senders)
email bodies, yielding `RawDoc` for each. Lazy-imports `googleapiclient` so
the rest of the app doesn't need it.

Live mode requires an OAuth token JSON for the account; absent that, the
factory returns None and the caller skips this source.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Source, SourceKind
from app.pollers.base import RawDoc

logger = logging.getLogger(__name__)


def build_gmail_service(token_json_path: str):
    """Build a credentialed Gmail API client. Lazy-imports `googleapiclient`."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(token_json_path)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailPoller:
    LABEL = "filed"
    QUERY = "has:attachment newer_than:7d -label:filed"

    def __init__(self, db: Session, source: Source, token_json_path: str):
        if source.kind != SourceKind.gmail:
            raise ValueError(f"Source {source.id} is not Gmail")
        self._db = db
        self._source = source
        self._service = build_gmail_service(token_json_path)
        self._label_id_cache: str | None = None

    def fetch(self) -> Iterable[RawDoc]:
        try:
            msgs = (
                self._service.users()
                .messages()
                .list(userId="me", q=self.QUERY, maxResults=50)
                .execute()
                .get("messages", [])
            )
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"list failed: {exc}")
            return

        for stub in msgs:
            try:
                yield from self._yield_from_message(stub["id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gmail msg %s skipped: %s", stub["id"], exc)
                continue
        self._record_success()

    def _yield_from_message(self, msg_id: str) -> Iterable[RawDoc]:
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from")
        received = _parse_internal_date(msg.get("internalDate"))

        attachments = list(_walk_attachments(msg["payload"]))
        if attachments:
            for part in attachments:
                data = self._fetch_part(msg_id, part)
                yield RawDoc(
                    data=data,
                    filename=part.get("filename") or f"{msg_id}.bin",
                    source_msg_ref=msg_id,
                    source_id=self._source.id,
                    mime_type=part.get("mimeType", "application/octet-stream"),
                    sender=sender,
                    received_at=received,
                )
            return

        # Body-allowlist path: no attachments, but the sender's body is
        # rendered to PDF by the pipeline if their address is allowlisted.
        if _sender_allowed(sender, self._source.body_allowlist):
            html = _extract_html(msg["payload"]) or _extract_text(msg["payload"]) or ""
            yield RawDoc(
                data=html.encode("utf-8"),
                filename=f"{msg_id}.html",          # pipeline will render to PDF
                source_msg_ref=msg_id,
                source_id=self._source.id,
                mime_type="text/html",
                sender=sender,
                received_at=received,
                extra={"body_only": True},
            )

    def _fetch_part(self, msg_id: str, part: dict) -> bytes:
        body = part.get("body", {})
        if body.get("data"):
            return base64.urlsafe_b64decode(body["data"])
        attachment_id = body["attachmentId"]
        att = (
            self._service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=msg_id, id=attachment_id)
            .execute()
        )
        return base64.urlsafe_b64decode(att["data"])

    def mark_processed(self, raw: RawDoc) -> None:
        try:
            label_id = self._ensure_filed_label()
            self._service.users().messages().modify(
                userId="me",
                id=raw.source_msg_ref,
                body={"addLabelIds": [label_id]},
            ).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not label Gmail %s: %s", raw.source_msg_ref, exc)

    def _ensure_filed_label(self) -> str:
        if self._label_id_cache:
            return self._label_id_cache
        labels = (
            self._service.users().labels().list(userId="me").execute().get("labels", [])
        )
        for label in labels:
            if label["name"] == self.LABEL:
                self._label_id_cache = label["id"]
                return label["id"]
        created = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={"name": self.LABEL, "labelListVisibility": "labelShow"},
            )
            .execute()
        )
        self._label_id_cache = created["id"]
        return created["id"]

    def _record_success(self) -> None:
        self._source.last_polled_at = datetime.now(tz=timezone.utc)
        self._source.last_error = None
        self._db.commit()

    def _record_error(self, message: str) -> None:
        self._source.last_error = message
        self._db.commit()


def _walk_attachments(payload: dict):
    if not payload:
        return
    parts = payload.get("parts") or [payload]
    for part in parts:
        if part.get("filename"):
            yield part
        if part.get("parts"):
            yield from _walk_attachments(part)


def _extract_html(payload: dict) -> str | None:
    for part in _walk_parts(payload):
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    return None


def _extract_text(payload: dict) -> str | None:
    for part in _walk_parts(payload):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    return None


def _walk_parts(payload: dict):
    if not payload:
        return
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _sender_allowed(sender: str | None, allowlist: list[str] | None) -> bool:
    if not sender or not allowlist:
        return False
    sender_lower = sender.lower()
    return any(needle.lower() in sender_lower for needle in allowlist if needle)


def _parse_internal_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ms = int(value)
    except (ValueError, TypeError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
