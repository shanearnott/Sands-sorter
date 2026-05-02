"""Demo Drive backend: writes files to a local directory using the same path
the real uploader would. The `file_id` is a sha256-prefix so it round-trips
through `processed_documents.drive_file_id` like the live API.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path, PurePosixPath

from app.drive.paths import folder_chain
from app.drive.uploader import DriveUpload

logger = logging.getLogger(__name__)


class FakeDriveUploader:
    """Drop-in replacement for `DriveUploader` that writes to local disk.

    `file_id` is `fake_<sha8>`; `web_view_link` is `file://<absolute path>`
    so the dashboard's "open" links work in a browser running on the same
    machine.
    """

    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def ensure_path(self, segments: list[str]) -> Path:
        target = self._root.joinpath(*segments)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def upload(
        self,
        *,
        path: PurePosixPath,
        data: bytes,
        mime_type: str | None = None,
    ) -> DriveUpload:
        chain = folder_chain(path)
        filename = path.parts[-1]
        parent = self.ensure_path(chain)
        target = parent / filename
        if target.exists() and target.read_bytes() == data:
            file_id = "fake_" + hashlib.sha256(data).hexdigest()[:8]
            return DriveUpload(
                file_id=file_id,
                drive_path=str(path),
                web_view_link=f"file://{target}",
            )

        target.write_bytes(data)
        file_id = "fake_" + hashlib.sha256(data).hexdigest()[:8]
        logger.info("FakeDriveUploader wrote %s (%d bytes)", target, len(data))
        return DriveUpload(
            file_id=file_id,
            drive_path=str(path),
            web_view_link=f"file://{target}",
        )
