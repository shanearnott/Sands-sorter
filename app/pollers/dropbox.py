"""Live Dropbox poller. Watches a folder for new bills/screenshots, advances
the cursor stored on `Source.cursor`, and moves filed items to
`<inbox>/processed/<YYYY-MM>/`.

Lazy-imports the `dropbox` SDK so the rest of the app doesn't pay for it.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.importer.sources import SUPPORTED_EXTENSIONS
from app.models import Source, SourceKind
from app.pollers.base import RawDoc

logger = logging.getLogger(__name__)


class DropboxPoller:
    def __init__(self, db: Session, source: Source, access_token: str, root_path: str):
        if source.kind != SourceKind.dropbox:
            raise ValueError(f"Source {source.id} is not Dropbox")
        from dropbox import Dropbox  # type: ignore

        self._db = db
        self._source = source
        self._client = Dropbox(access_token)
        self._root = root_path.rstrip("/")

    def fetch(self) -> Iterable[RawDoc]:
        from dropbox.files import FolderMetadata  # type: ignore

        try:
            entries: list = []
            if self._source.cursor:
                page = self._client.files_list_folder_continue(self._source.cursor)
            else:
                page = self._client.files_list_folder(self._root, recursive=True)
            entries.extend(page.entries)
            while page.has_more:
                page = self._client.files_list_folder_continue(page.cursor)
                entries.extend(page.entries)
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"list failed: {exc}")
            return

        for entry in entries:
            if isinstance(entry, FolderMetadata):
                continue
            name = entry.name
            if PurePosixPath(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                _md, response = self._client.files_download(entry.path_lower)
                yield RawDoc(
                    data=response.content,
                    filename=name,
                    source_msg_ref=entry.path_lower,
                    source_id=self._source.id,
                    mime_type=_guess_mime(name),
                    received_at=getattr(entry, "client_modified", None),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Dropbox %s skipped: %s", entry.path_lower, exc)

        self._source.cursor = page.cursor
        self._record_success()

    def mark_processed(self, raw: RawDoc) -> None:
        moved_at = (raw.received_at or datetime.now(tz=timezone.utc)).strftime("%Y-%m")
        target = f"{self._root}/processed/{moved_at}/{PurePosixPath(raw.source_msg_ref).name}"
        try:
            self._client.files_move_v2(raw.source_msg_ref, target, autorename=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not move Dropbox %s: %s", raw.source_msg_ref, exc)

    def _record_success(self) -> None:
        self._source.last_polled_at = datetime.now(tz=timezone.utc)
        self._source.last_error = None
        self._db.commit()

    def _record_error(self, message: str) -> None:
        self._source.last_error = message
        self._db.commit()


def _guess_mime(name: str) -> str:
    import mimetypes
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
