from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath

from app.models import ScopeCountry, ScopeKind
from app.utils.fy import financial_year, fy_label

UNSORTED_ROOT = "Unsorted"

KIND_ROOT = {
    ScopeKind.property: "Properties",
    ScopeKind.personal: "Personal",
    ScopeKind.entity: "Entities",
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

    Layout (FY is metadata only, not a folder layer):
      property -> Properties/<Name>/<Category>/<file>
      personal -> Personal/<Category>/<file>             (singleton, no name)
      entity   -> Entities/<Name>/<Category>/<file>      (trusts + companies)
      missing  -> Unsorted/<file>                         (low-confidence fallback)

    The FY end-year is computed from `doc_date` + `scope_country` and returned
    so callers can record it on `processed_documents.financial_year`.
    """
    when = now or datetime.utcnow()
    effective_date = doc_date or when.date()
    country = scope_country or ScopeCountry.AU
    fy_end = financial_year(effective_date, country)

    if scope_kind is None or category_name is None:
        return PurePosixPath(UNSORTED_ROOT, filename), fy_end

    root = KIND_ROOT[scope_kind]
    category = safe_segment(category_name)

    if scope_kind == ScopeKind.personal:
        return PurePosixPath(root, category, filename), fy_end

    if not scope_name:
        return PurePosixPath(UNSORTED_ROOT, filename), fy_end
    return (
        PurePosixPath(root, safe_segment(scope_name), category, filename),
        fy_end,
    )


# Re-export for templates / call sites that want the FY label string ("FY2026").
__all__ = ["build_path", "fy_label", "safe_segment", "folder_chain", "UNSORTED_ROOT", "KIND_ROOT"]


def folder_chain(path: PurePosixPath) -> list[str]:
    """Return the folder segments leading up to (but not including) the file name."""
    return list(path.parts[:-1])
