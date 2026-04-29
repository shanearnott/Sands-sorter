from __future__ import annotations

import io
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.drive.paths import folder_chain

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class DriveUpload:
    file_id: str
    drive_path: str
    web_view_link: str | None


class DriveUploader:
    """Idempotent uploader. Resolves the folder chain under root_folder_id, creating
    missing folders, then uploads the file. Caches folder ids per instance."""

    def __init__(self, credentials, root_folder_id: str):
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._root = root_folder_id
        self._folder_cache: dict[tuple[str, str], str] = {}

    def _find_child(self, parent_id: str, name: str, *, mime_type: str | None) -> str | None:
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and '{parent_id}' in parents and trashed = false"
            + (f" and mimeType = '{mime_type}'" if mime_type else "")
        )
        resp = (
            self._service.files()
            .list(q=q, fields="files(id,name,mimeType)", pageSize=1, spaces="drive")
            .execute()
        )
        items = resp.get("files", [])
        return items[0]["id"] if items else None

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        cache_key = (parent_id, name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        existing = self._find_child(parent_id, name, mime_type=FOLDER_MIME)
        if existing:
            self._folder_cache[cache_key] = existing
            return existing

        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        created = self._service.files().create(body=body, fields="id").execute()
        folder_id = created["id"]
        self._folder_cache[cache_key] = folder_id
        logger.info("Created Drive folder %s under %s -> %s", name, parent_id, folder_id)
        return folder_id

    def ensure_path(self, segments: list[str]) -> str:
        parent = self._root
        for segment in segments:
            parent = self._ensure_folder(parent, segment)
        return parent

    def upload(
        self,
        *,
        path: PurePosixPath,
        data: bytes,
        mime_type: str | None = None,
    ) -> DriveUpload:
        chain = folder_chain(path)
        filename = path.parts[-1]
        parent_id = self.ensure_path(chain)

        if (existing := self._find_child(parent_id, filename, mime_type=None)) is not None:
            file = (
                self._service.files()
                .get(fileId=existing, fields="id,webViewLink")
                .execute()
            )
            return DriveUpload(
                file_id=file["id"],
                drive_path=str(path),
                web_view_link=file.get("webViewLink"),
            )

        guessed = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=guessed, resumable=False)
        body = {"name": filename, "parents": [parent_id]}
        created = (
            self._service.files()
            .create(body=body, media_body=media, fields="id,webViewLink")
            .execute()
        )
        return DriveUpload(
            file_id=created["id"],
            drive_path=str(path),
            web_view_link=created.get("webViewLink"),
        )
