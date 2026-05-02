"""M5 surfaces: cross-scope /overview, JSON endpoints for Chart.js, CSV/XLSX export."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.config import get_settings
from app.db import get_db
from app.drive.paths import safe_segment
from app.models import Direction, ProcessedDocument, Scope
from app.reports import export as exporter
from app.reports import queries

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _ctx(request: Request, **extra) -> dict:
    return {
        "request": request,
        "user_email": optional_user(request),
        "settings": get_settings(),
        **extra,
    }


# --- Cross-scope overview --------------------------------------------------

@router.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    fy: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    fys = sorted(
        {
            row[0]
            for row in db.execute(
                select(ProcessedDocument.financial_year).where(
                    ProcessedDocument.financial_year.is_not(None)
                )
            )
        },
        reverse=True,
    )
    scopes = list(db.scalars(select(Scope).order_by(Scope.kind, Scope.name)))
    return templates.TemplateResponse(
        "overview.html",
        _ctx(request, fy=fy, fys=fys, scopes=scopes),
    )


# --- JSON endpoints driving Chart.js ---------------------------------------

@router.get("/reports/{scope_id}/monthly.json")
def reports_monthly(
    scope_id: int,
    fy: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return JSONResponse(queries.monthly_totals(db, scope_id=scope_id, fy=fy))


@router.get("/reports/{scope_id}/by_category.json")
def reports_by_category(
    scope_id: int,
    fy: int | None = Query(default=None),
    direction: Direction = Query(default=Direction.expense),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return JSONResponse(
        queries.by_category(db, scope_id=scope_id, fy=fy, direction=direction)
    )


@router.get("/reports/{scope_id}/top_vendors.json")
def reports_top_vendors(
    scope_id: int,
    fy: int | None = Query(default=None),
    direction: Direction = Query(default=Direction.expense),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return JSONResponse(
        queries.top_vendors(db, scope_id=scope_id, fy=fy, direction=direction)
    )


@router.get("/reports/{scope_id}/vendor_trend.json")
def reports_vendor_trend(
    scope_id: int,
    fy: int | None = Query(default=None),
    direction: Direction = Query(default=Direction.expense),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return JSONResponse(
        queries.vendor_trend(db, scope_id=scope_id, fy=fy, direction=direction)
    )


@router.get("/reports/overview.json")
def reports_overview(
    fy: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return JSONResponse(queries.cross_scope_overview(db, fy=fy))


# --- Export ----------------------------------------------------------------

@router.get("/scopes/{scope_id}/export.csv")
def export_csv(
    scope_id: int,
    fy: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    scope = db.get(Scope, scope_id)
    if not scope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scope not found")
    rows = queries.table(db, scope_id=scope_id, fy=fy)
    fy_part = f"-FY{fy}" if fy else ""
    fname = f"{safe_segment(scope.name)}{fy_part}.csv"
    return Response(
        content=exporter.to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/scopes/{scope_id}/export.xlsx")
def export_xlsx(
    scope_id: int,
    fy: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    scope = db.get(Scope, scope_id)
    if not scope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scope not found")
    rows = queries.table(db, scope_id=scope_id, fy=fy)
    try:
        data = exporter.to_xlsx(rows)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    fy_part = f"-FY{fy}" if fy else ""
    fname = f"{safe_segment(scope.name)}{fy_part}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
