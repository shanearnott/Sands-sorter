from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath

from app.models import ScopeCountry, ScopeKind
from app.utils.fy import financial_year, fy_label

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
    scope_country: ScopeCountry | None,
    category_name: str | None,
    doc_date: date | None,
    filename: str,
    now: datetime | None = None,
) -> tuple[PurePosixPath, int]:
    """Build the Drive path and return `(path, financial_year_end)`.

    Layout:
      property -> Properties/<Name>/<Category>/FY<YYYY>/<file>
      car      -> Cars/<Name>/<Category>/FY<YYYY>/<file>
      life     -> Life/<Category>/FY<YYYY>/<file>
      missing  -> Unsorted/FY<YYYY>/<file>            (low-confidence fallback)

    The FY end-year is computed from `doc_date` + `scope_country`. When either
    is missing we fall back to today's date treated as AU.
    """
    when = now or datetime.utcnow()
    effective_date = doc_date or when.date()
    country = scope_country or ScopeCountry.AU
    fy_end = financial_year(effective_date, country)
    fy = fy_label(fy_end)

    if scope_kind is None or category_name is None:
        return PurePosixPath(UNSORTED_ROOT, fy, filename), fy_end

    root = KIND_ROOT[scope_kind]
    category = safe_segment(category_name)

    if scope_kind == ScopeKind.life:
        return PurePosixPath(root, category, fy, filename), fy_end

    if not scope_name:
        return PurePosixPath(UNSORTED_ROOT, fy, filename), fy_end
    return (
        PurePosixPath(root, safe_segment(scope_name), category, fy, filename),
        fy_end,
    )


def folder_chain(path: PurePosixPath) -> list[str]:
    """Return the folder segments leading up to (but not including) the file name."""
    return list(path.parts[:-1])
