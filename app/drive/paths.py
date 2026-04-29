from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

UNSORTED = "Unsorted"
PROPERTIES_ROOT = "Properties"

_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_segment(text: str) -> str:
    """Strip characters Drive folder names handle poorly. Trims and collapses whitespace."""
    cleaned = _SAFE.sub("", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Unknown"


def build_path(
    *,
    property_name: str | None,
    category_name: str | None,
    year: int | None,
    filename: str,
    now: datetime | None = None,
) -> PurePosixPath:
    """Build the Drive path. Falls back to Unsorted when classification is missing."""
    when = now or datetime.utcnow()
    yr = year or when.year

    if property_name and category_name:
        return PurePosixPath(
            PROPERTIES_ROOT,
            safe_segment(property_name),
            safe_segment(category_name),
            str(yr),
            filename,
        )
    return PurePosixPath(PROPERTIES_ROOT, UNSORTED, str(yr), filename)


def folder_chain(path: PurePosixPath) -> list[str]:
    """Return the folder segments leading up to (but not including) the file name."""
    return list(path.parts[:-1])
