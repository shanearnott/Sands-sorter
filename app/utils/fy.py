from __future__ import annotations

from datetime import date

from app.models import ScopeCountry

FY_START_MONTH = {
    ScopeCountry.AU: 7,   # 1 July
    ScopeCountry.US: 1,   # 1 January (calendar)
}


def financial_year(doc_date: date, country: ScopeCountry) -> int:
    """Return the FY end-year for `doc_date` under the given country.

    AU: doc dated 2025-09-01 -> FY ends 30 June 2026 -> 2026.
    AU: doc dated 2025-06-30 -> FY ends 30 June 2025 -> 2025.
    US: doc dated 2025-09-01 -> calendar FY -> 2025.
    """
    start_month = FY_START_MONTH[country]
    if doc_date.month >= start_month and start_month != 1:
        return doc_date.year + 1
    return doc_date.year


def fy_label(end_year: int) -> str:
    return f"FY{end_year}"
