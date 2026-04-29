from __future__ import annotations

import logging
import mimetypes
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# File extensions we treat as billable documents during import. Everything else
# in the source tree (.txt notes, .DS_Store, .key/.pem) is skipped.
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".heic",
    ".webp",
}


@dataclass(frozen=True)
class SourceItem:
    """One file discovered in a source tree, ready to be processed.

    `bytes_loader` is a zero-arg callable so we don't fetch large file bodies
    until the wizard actually needs them. `relative_path` is the path under
    the source root and feeds the folder-name heuristic for scope/category
    proposals.
    """

    relative_path: str           # e.g. "Beach House/Electricity/2024/origin-energy.pdf"
    filename: str                # leaf
    mime_type: str
    modified_time: datetime | None
    bytes_loader: Callable[[], bytes]


class TreeSource(Protocol):
    """Lazily yield `SourceItem`s under a root."""

    def iter_items(self) -> Iterator[SourceItem]: ...


def _is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def _guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# --- Local tree (uploaded zip already unpacked, or a path on disk) ----------

class LocalTreeSource:
    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        if not self._root.exists():
            raise FileNotFoundError(self._root)

    def iter_items(self) -> Iterator[SourceItem]:
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or not _is_supported(path.name):
                continue
            relative = path.relative_to(self._root).as_posix()
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            yield SourceItem(
                relative_path=relative,
                filename=path.name,
                mime_type=_guess_mime(path.name),
                modified_time=modified,
                bytes_loader=_make_local_loader(path),
            )


def _make_local_loader(path: Path) -> Callable[[], bytes]:
    def _load() -> bytes:
        return path.read_bytes()
    return _load


# --- Drive recursive listing ------------------------------------------------

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveTreeSource:
    """Walks a Drive folder recursively. Caller supplies a credentialed Drive
    service (built via `googleapiclient.discovery.build`)."""

    def __init__(self, service, root_folder_id: str):
        self._service = service
        self._root = root_folder_id

    def iter_items(self) -> Iterator[SourceItem]:
        yield from self._walk(self._root, prefix="")

    def _walk(self, folder_id: str, *, prefix: str) -> Iterator[SourceItem]:
        page_token: str | None = None
        while True:
            resp = (
                self._service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id,name,mimeType,modifiedTime)",
                    pageSize=200,
                    pageToken=page_token,
                    spaces="drive",
                )
                .execute()
            )
            for entry in resp.get("files", []):
                name = entry["name"]
                child_path = f"{prefix}/{name}" if prefix else name
                if entry["mimeType"] == DRIVE_FOLDER_MIME:
                    yield from self._walk(entry["id"], prefix=child_path)
                    continue
                if not _is_supported(name):
                    continue
                modified = _parse_iso(entry.get("modifiedTime"))
                yield SourceItem(
                    relative_path=child_path,
                    filename=name,
                    mime_type=entry["mimeType"] or _guess_mime(name),
                    modified_time=modified,
                    bytes_loader=_make_drive_loader(self._service, entry["id"]),
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                return


def _make_drive_loader(service, file_id: str) -> Callable[[], bytes]:
    def _load() -> bytes:
        # Imported lazily to avoid forcing the dep at import time.
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore
        import io

        request = service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        return buffer.getvalue()
    return _load


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- Folder-name heuristics -------------------------------------------------

def folder_segments(relative_path: str) -> list[str]:
    """Return the folder segments leading up to (but not including) the file."""
    parts = relative_path.split("/")
    return parts[:-1]
