"""Per-scope dashboard + bulk download by financial year.

Lives in its own module so the M5 chart layer can attach to the same routes
without rewriting the M1/M2 surfaces.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.config import get_settings, is_local_mode
from app.db import get_db
from app.drive.credentials import load_drive_credentials
from app.drive.download import iter_file_bytes
from app.drive.paths import safe_segment
from app.models import Category, DocStatus, ProcessedDocument, Scope

logger = logging.getLogger(__name__)
router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "user_email": optional_user(request),
        "settings": get_settings(),
        "local_mode": is_local_mode(),
        **extra,
    }


@dataclass(frozen=True)
class FYGroup:
    fy: int            # FY end-year, e.g. 2026
    label: str         # "FY2026"
    docs: list[ProcessedDocument]


def group_by_fy(docs: list[ProcessedDocument]) -> list[FYGroup]:
    """Group filed documents by `financial_year`, descending. Documents
    missing an FY are bucketed under year 0 (label `Unknown FY`)."""
    bucket: dict[int, list[ProcessedDocument]] = defaultdict(list)
    for d in docs:
        bucket[d.financial_year or 0].append(d)
    out: list[FYGroup] = []
    for fy in sorted(bucket.keys(), reverse=True):
        label = f"FY{fy}" if fy else "Unknown FY"
        out.append(FYGroup(fy=fy, label=label, docs=bucket[fy]))
    return out


@router.get("/scopes/{scope_id}", response_class=HTMLResponse)
def scope_detail(
    scope_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    scope = db.get(Scope, scope_id)
    if scope is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scope not found")

    docs = list(
        db.scalars(
            select(ProcessedDocument)
            .where(ProcessedDocument.scope_id == scope.id)
            .where(ProcessedDocument.status == DocStatus.filed)
            .order_by(
                ProcessedDocument.financial_year.desc().nullslast(),
                ProcessedDocument.doc_date.desc().nullslast(),
                ProcessedDocument.id.desc(),
            )
        )
    )
    groups = group_by_fy(docs)
    cat_lookup = {c.id: c for c in db.scalars(select(Category))}
    return templates.TemplateResponse(
        "scope_detail.html",
        _ctx(request, scope=scope, groups=groups, categories=cat_lookup),
    )


@router.get("/scopes/{scope_id}/download.zip")
def scope_download_zip(
    scope_id: int,
    fy: int,
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    scope = db.get(Scope, scope_id)
    if scope is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scope not found")

    docs = list(
        db.scalars(
            select(ProcessedDocument)
            .where(ProcessedDocument.scope_id == scope.id)
            .where(ProcessedDocument.financial_year == fy)
            .where(ProcessedDocument.status == DocStatus.filed)
            .where(ProcessedDocument.drive_file_id.is_not(None))
            .order_by(ProcessedDocument.id.asc())
        )
    )
    if not docs:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No filed documents for FY{fy} in this scope"
        )

    cat_lookup = {c.id: c for c in db.scalars(select(Category))}
    service = _build_drive_service()

    fname = f"{safe_segment(scope.name)}-FY{fy}.zip"
    zip_iter = _build_zip_stream(docs, cat_lookup, service)

    return StreamingResponse(
        zip_iter,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# --- helpers ---------------------------------------------------------------

def _build_drive_service():
    """Build a credentialed Drive v3 service. Lazy-imports googleapiclient."""
    from googleapiclient.discovery import build

    creds = load_drive_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _build_zip_stream(
    docs: list[ProcessedDocument],
    cat_lookup: dict[int, Category],
    service,
) -> Iterator[bytes]:
    from zipstream import ZipStream

    zs = ZipStream()
    seen_names: set[str] = set()
    for doc in docs:
        cat = cat_lookup.get(doc.category_id) if doc.category_id else None
        category_segment = safe_segment(cat.name) if cat else "Uncategorised"
        base = doc.original_filename or f"document-{doc.id}.bin"
        arcname = _disambiguate(f"{category_segment}/{base}", seen_names)
        seen_names.add(arcname)
        zs.add(iter_file_bytes(service, doc.drive_file_id), arcname=arcname)
    yield from zs


def _disambiguate(name: str, seen: set[str]) -> str:
    """If a zip entry name collides, append a numeric suffix before the extension."""
    if name not in seen:
        return name
    stem, dot, ext = name.rpartition(".")
    n = 2
    while True:
        candidate = f"{stem}-{n}.{ext}" if dot else f"{name}-{n}"
        if candidate not in seen:
            return candidate
        n += 1
