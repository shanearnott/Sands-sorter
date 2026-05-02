from __future__ import annotations

import io
from collections.abc import Iterator


def iter_file_bytes(service, file_id: str, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    """Stream a Drive file's bytes in chunks via the Drive v3 download API.

    Caller passes a credentialed `googleapiclient.discovery` service (built
    elsewhere with `load_drive_credentials()`). The returned generator yields
    bytes payloads suitable for piping straight into `zipstream-ng` without
    buffering the whole file in memory.
    """
    # Imported lazily so unit tests don't need google libs unless they use this.
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=chunk_size)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
        chunk = buffer.getvalue()
        if chunk:
            yield chunk
            buffer.seek(0)
            buffer.truncate(0)
