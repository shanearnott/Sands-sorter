"""CSV and XLSX export of the per-scope bills table."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from app.reports.queries import TableRow


CSV_HEADERS = [
    "doc_id",
    "doc_date",
    "counterparty",
    "category",
    "direction",
    "amount",
    "currency",
    "drive_file_id",
]


def to_csv(rows: Iterable[TableRow]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    for r in rows:
        writer.writerow(
            [
                r.doc_id,
                r.doc_date or "",
                r.counterparty or "",
                r.category or "",
                r.direction,
                f"{r.amount_cents/100:.2f}" if r.amount_cents else "",
                r.currency or "",
                r.drive_file_id or "",
            ]
        )
    return buffer.getvalue().encode("utf-8")


def to_xlsx(rows: Iterable[TableRow]) -> bytes:
    """Lazy-import openpyxl since it's an optional dep."""
    try:
        from openpyxl import Workbook  # type: ignore
    except ImportError:
        # Fall back to CSV bytes wrapped in a tiny .xlsx-like blob isn't possible;
        # callers should check availability or accept CSV.
        raise RuntimeError("openpyxl not installed; install it for XLSX export.")

    wb = Workbook()
    ws = wb.active
    ws.append(CSV_HEADERS)
    for r in rows:
        ws.append(
            [
                r.doc_id,
                r.doc_date or "",
                r.counterparty or "",
                r.category or "",
                r.direction,
                (r.amount_cents / 100) if r.amount_cents else None,
                r.currency or "",
                r.drive_file_id or "",
            ]
        )
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
