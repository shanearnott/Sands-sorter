"""Demo Dropbox poller. Watches a local folder for new bills/screenshots.
Once filed, files are moved to `<root>/processed/<YYYY-MM>/`.

Mirrors the live `DropboxPoller` interface so `/internal/poll` can dispatch
the same way regardless of credentials.
"""
from __future__ import annotations

import logging
import mimetypes
import shutil
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.importer.sources import SUPPORTED_EXTENSIONS
from app.models import Source
from app.pollers.base import RawDoc

logger = logging.getLogger(__name__)


class LocalDropboxPoller:
    def __init__(self, db: Session, source: Source, root: str | Path):
        self._db = db
        self._source = source
        self._root = Path(root).resolve()
        self._inbox = self._root / "inbox"
        self._processed = self._root / "processed"
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._processed.mkdir(parents=True, exist_ok=True)

    def fetch(self) -> Iterable[RawDoc]:
        for path in sorted(self._inbox.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                yield RawDoc(
                    data=path.read_bytes(),
                    filename=path.name,
                    source_msg_ref=str(path.relative_to(self._inbox)),
                    source_id=self._source.id,
                    mime_type=mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                    received_at=datetime.fromtimestamp(
                        path.stat().st_mtime, tz=timezone.utc
                    ),
                )
            except OSError as exc:
                logger.warning("local %s skipped: %s", path, exc)
        self._source.last_polled_at = datetime.now(tz=timezone.utc)
        self._source.last_error = None
        self._db.commit()

    def mark_processed(self, raw: RawDoc) -> None:
        src = self._inbox / raw.source_msg_ref
        if not src.exists():
            return
        bucket = (raw.received_at or datetime.now(tz=timezone.utc)).strftime("%Y-%m")
        dest = self._processed / bucket / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dest))
        except OSError as exc:
            logger.warning("Could not move %s: %s", src, exc)
