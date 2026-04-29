from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

from app.models import ScopeKind

UNSORTED_ROOT = "Unsorted"

KIND_ROOT = {
    ScopeKind.property: "Properties",
    ScopeKind.car: "Cars",
    ScopeKind.life: "Life",
}

_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_segment(text: str) -> str:
    """Strip characters Drive folder names handle poorly. Trims and collapses whitespace."""
    cleaned = _SAFE.sub("", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Unknown"


def build_path(
    *,
    scope_kind: ScopeKind | None,
    scope_name: str | None,
    category_name: str | None,
    year: int | None,
    filename: str,
    now: datetime | None = None,
) -> PurePosixPath:
    """Build the Drive path. Top-level folder depends on the scope kind:

    - property -> Properties/<Name>/<Category>/<Year>/<file>
    - car      -> Cars/<Name>/<Category>/<Year>/<file>
    - life     -> Life/<Category>/<Year>/<file>          (singleton, no name)
    - missing  -> Unsorted/<Year>/<file>                  (low-confidence fallback)
    """
    when = now or datetime.utcnow()
    yr = year or when.year

    if scope_kind is None or category_name is None:
        return PurePosixPath(UNSORTED_ROOT, str(yr), filename)

    root = KIND_ROOT[scope_kind]
    category = safe_segment(category_name)

    if scope_kind == ScopeKind.life:
        return PurePosixPath(root, category, str(yr), filename)

    if not scope_name:
        return PurePosixPath(UNSORTED_ROOT, str(yr), filename)
    return PurePosixPath(root, safe_segment(scope_name), category, str(yr), filename)


def folder_chain(path: PurePosixPath) -> list[str]:
    """Return the folder segments leading up to (but not including) the file name."""
    return list(path.parts[:-1])
