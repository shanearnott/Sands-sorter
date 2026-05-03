from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.config import get_settings, is_local_mode
from app.db import get_db
from app.drive.credentials import load_drive_credentials
from app.drive.paths import build_path
from app.drive.uploader import DriveUploader
from app.models import (
    PERSONAL_SCOPE_NAME,
    Category,
    Classifier,
    Direction,
    DocStatus,
    ProcessedDocument,
    Scope,
    ScopeCountry,
    ScopeKind,
)
from app.utils.hashing import sha256_bytes

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


def _get_personal_scope(db: Session) -> Scope:
    """Return the Personal singleton, creating it if absent (e.g. on a fresh dev DB)."""
    personal = db.scalar(select(Scope).where(Scope.kind == ScopeKind.personal))
    if personal is None:
        personal = Scope(kind=ScopeKind.personal, name=PERSONAL_SCOPE_NAME, active=True)
        db.add(personal)
        db.commit()
        db.refresh(personal)
    return personal


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func

    from app.models import Direction, DocStatus, DocumentExtraction

    user = optional_user(request)
    if not user:
        return templates.TemplateResponse(request, "login.html", _ctx(request))

    recent = list(
        db.scalars(
            select(ProcessedDocument)
            .order_by(ProcessedDocument.created_at.desc())
            .limit(20)
        )
    )

    # KPI stats — last 30 days vs the 30 days before that.
    now = datetime.now(tz=timezone.utc)
    cutoff_30 = now - timedelta(days=30)
    cutoff_60 = now - timedelta(days=60)

    def _count(*conds):
        stmt = select(func.count(ProcessedDocument.id))
        for c in conds:
            stmt = stmt.where(c)
        return db.scalar(stmt) or 0

    def _amount_sum(*conds):
        stmt = (
            select(func.coalesce(func.sum(DocumentExtraction.amount_cents), 0))
            .join(
                ProcessedDocument,
                ProcessedDocument.id == DocumentExtraction.document_id,
            )
        )
        for c in conds:
            stmt = stmt.where(c)
        return db.scalar(stmt) or 0

    filed_30 = _count(
        ProcessedDocument.status == DocStatus.filed,
        ProcessedDocument.created_at >= cutoff_30,
    )
    filed_30_prev = _count(
        ProcessedDocument.status == DocStatus.filed,
        ProcessedDocument.created_at >= cutoff_60,
        ProcessedDocument.created_at < cutoff_30,
    )

    unsorted_open = _count(ProcessedDocument.status == DocStatus.unsorted)

    income_30 = _amount_sum(
        ProcessedDocument.status == DocStatus.filed,
        ProcessedDocument.direction == Direction.income,
        ProcessedDocument.created_at >= cutoff_30,
    )
    expense_30 = _amount_sum(
        ProcessedDocument.status == DocStatus.filed,
        ProcessedDocument.direction == Direction.expense,
        ProcessedDocument.created_at >= cutoff_30,
    )

    kpis = {
        "filed_30": filed_30,
        "filed_30_prev": filed_30_prev,
        "unsorted_open": unsorted_open,
        "income_30_cents": income_30,
        "expense_30_cents": expense_30,
        "net_30_cents": income_30 - expense_30,
    }

    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, recent=recent, kpis=kpis)
    )


# --- Scopes (Properties + Entities share a single CRUD page, kind-filtered) ---

def _scope_list_response(
    request: Request, db: Session, kind: ScopeKind, page_title: str, route_path: str
):
    items = list(
        db.scalars(
            select(Scope).where(Scope.kind == kind).order_by(Scope.name)
        )
    )
    return templates.TemplateResponse(request, "scopes.html", _ctx(
            request,
            kind=kind,
            scopes=items,
            page_title=page_title,
            route_path=route_path,
        ),
    )


@router.get("/properties", response_class=HTMLResponse)
def properties_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _scope_list_response(request, db, ScopeKind.property, "Properties", "/properties")


@router.post("/properties")
def properties_create(
    name: str = Form(...),
    country: ScopeCountry = Form(ScopeCountry.AU),
    drive_folder_id: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return _create_scope(db, ScopeKind.property, name, country, drive_folder_id, "/properties")


@router.post("/properties/{scope_id}/delete")
def properties_delete(
    scope_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _delete_scope(db, scope_id, ScopeKind.property, "/properties")


@router.get("/entities", response_class=HTMLResponse)
def entities_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _scope_list_response(request, db, ScopeKind.entity, "Entities", "/entities")


@router.post("/entities")
def entities_create(
    name: str = Form(...),
    country: ScopeCountry = Form(ScopeCountry.AU),
    drive_folder_id: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return _create_scope(db, ScopeKind.entity, name, country, drive_folder_id, "/entities")


@router.post("/entities/{scope_id}/delete")
def entities_delete(
    scope_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _delete_scope(db, scope_id, ScopeKind.entity, "/entities")


def _create_scope(
    db: Session,
    kind: ScopeKind,
    name: str,
    country: ScopeCountry,
    drive_folder_id: str,
    redirect_to: str,
):
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is required")
    db.add(
        Scope(
            kind=kind,
            name=name,
            country=country,
            drive_folder_id=drive_folder_id.strip() or None,
        )
    )
    db.commit()
    return RedirectResponse(url=redirect_to, status_code=302)


def _delete_scope(db: Session, scope_id: int, expected_kind: ScopeKind, redirect_to: str):
    scope = db.get(Scope, scope_id)
    if scope and scope.kind == expected_kind:
        db.delete(scope)
        db.commit()
    return RedirectResponse(url=redirect_to, status_code=302)


@router.get("/personal", response_class=HTMLResponse)
def personal_view(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    personal = _get_personal_scope(db)
    return templates.TemplateResponse(request, "personal.html", _ctx(request, personal=personal))


@router.post("/personal")
def personal_update(
    country: ScopeCountry = Form(ScopeCountry.AU),
    drive_folder_id: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    personal = _get_personal_scope(db)
    personal.country = country
    personal.drive_folder_id = drive_folder_id.strip() or None
    db.commit()
    return RedirectResponse(url="/personal", status_code=302)


# --- Categories CRUD ---

@router.get("/categories", response_class=HTMLResponse)
def categories_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(db.scalars(select(Category).order_by(Category.sort_order, Category.name)))
    return templates.TemplateResponse(request, "categories.html", _ctx(request, categories=items))


@router.post("/categories")
def categories_create(
    name: str = Form(...),
    sort_order: int = Form(0),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is required")
    db.add(Category(name=name, sort_order=sort_order))
    db.commit()
    return RedirectResponse(url="/categories", status_code=302)


@router.post("/categories/{cat_id}/delete")
def categories_delete(
    cat_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    c = db.get(Category, cat_id)
    if c:
        db.delete(c)
        db.commit()
    return RedirectResponse(url="/categories", status_code=302)


# --- Manual upload ---

@router.get("/upload", response_class=HTMLResponse)
def upload_form(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    _get_personal_scope(db)  # ensure singleton exists
    scopes = list(
        db.scalars(
            select(Scope)
            .where(Scope.active.is_(True))
            .order_by(Scope.kind, Scope.name)
        )
    )
    categories = list(
        db.scalars(select(Category).order_by(Category.sort_order, Category.name))
    )
    return templates.TemplateResponse(request, "upload.html", _ctx(request, scopes=scopes, categories=categories)
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    scope_id: int = Form(...),
    category_id: int = Form(...),
    direction: Direction = Form(Direction.expense),
    doc_date: str = Form(""),
    file: UploadFile = ...,
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    scope = db.get(Scope, scope_id)
    cat = db.get(Category, category_id)
    if not scope or not cat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown scope or category")

    parsed_date: date | None
    if doc_date:
        try:
            parsed_date = date.fromisoformat(doc_date)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "doc_date must be YYYY-MM-DD"
            ) from exc
    else:
        parsed_date = None

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    digest = sha256_bytes(data)
    existing = db.scalar(select(ProcessedDocument).where(ProcessedDocument.sha256 == digest))
    if existing:
        return templates.TemplateResponse(request, "upload_result.html", _ctx(request, duplicate=True, doc=existing, link=None),
        )

    path, fy_end = build_path(
        scope_kind=scope.kind,
        scope_name=scope.name,
        scope_country=scope.country,
        category_name=cat.name,
        doc_date=parsed_date,
        filename=file.filename or f"upload-{digest[:8]}",
    )

    creds = load_drive_credentials()
    uploader = DriveUploader(creds, get_settings().drive_root_folder_id)
    result = uploader.upload(path=path, data=data, mime_type=file.content_type)

    doc = ProcessedDocument(
        sha256=digest,
        original_filename=file.filename or "",
        classifier=Classifier.manual,
        scope_id=scope.id,
        category_id=cat.id,
        direction=direction,
        doc_date=parsed_date,
        financial_year=fy_end,
        drive_file_id=result.file_id,
        drive_path=result.drive_path,
        confidence=1.0,
        status=DocStatus.filed,
        filed_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return templates.TemplateResponse(request, "upload_result.html", _ctx(request, doc=doc, link=result.web_view_link, duplicate=False),
    )
