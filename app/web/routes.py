from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.config import get_settings
from app.db import get_db
from app.drive.credentials import load_drive_credentials
from app.drive.paths import build_path
from app.drive.uploader import DriveUploader
from app.models import (
    LIFE_SCOPE_NAME,
    Category,
    Classifier,
    DocStatus,
    ProcessedDocument,
    Scope,
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
        **extra,
    }


def _get_life_scope(db: Session) -> Scope:
    """Return the Life singleton, creating it if absent (e.g. on a fresh dev DB)."""
    life = db.scalar(select(Scope).where(Scope.kind == ScopeKind.life))
    if life is None:
        life = Scope(kind=ScopeKind.life, name=LIFE_SCOPE_NAME, active=True)
        db.add(life)
        db.commit()
        db.refresh(life)
    return life


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = optional_user(request)
    if not user:
        return templates.TemplateResponse("login.html", _ctx(request))

    recent = list(
        db.scalars(
            select(ProcessedDocument)
            .order_by(ProcessedDocument.created_at.desc())
            .limit(20)
        )
    )
    return templates.TemplateResponse("dashboard.html", _ctx(request, recent=recent))


# --- Scopes (Properties + Cars share a single CRUD page, kind-filtered) ---

def _scope_list_response(
    request: Request, db: Session, kind: ScopeKind, page_title: str, route_path: str
):
    items = list(
        db.scalars(
            select(Scope).where(Scope.kind == kind).order_by(Scope.name)
        )
    )
    return templates.TemplateResponse(
        "scopes.html",
        _ctx(
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
    drive_folder_id: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return _create_scope(db, ScopeKind.property, name, drive_folder_id, "/properties")


@router.post("/properties/{scope_id}/delete")
def properties_delete(
    scope_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _delete_scope(db, scope_id, ScopeKind.property, "/properties")


@router.get("/cars", response_class=HTMLResponse)
def cars_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _scope_list_response(request, db, ScopeKind.car, "Cars", "/cars")


@router.post("/cars")
def cars_create(
    name: str = Form(...),
    drive_folder_id: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return _create_scope(db, ScopeKind.car, name, drive_folder_id, "/cars")


@router.post("/cars/{scope_id}/delete")
def cars_delete(
    scope_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    return _delete_scope(db, scope_id, ScopeKind.car, "/cars")


def _create_scope(
    db: Session, kind: ScopeKind, name: str, drive_folder_id: str, redirect_to: str
):
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is required")
    db.add(Scope(kind=kind, name=name, drive_folder_id=drive_folder_id.strip() or None))
    db.commit()
    return RedirectResponse(url=redirect_to, status_code=302)


def _delete_scope(db: Session, scope_id: int, expected_kind: ScopeKind, redirect_to: str):
    scope = db.get(Scope, scope_id)
    if scope and scope.kind == expected_kind:
        db.delete(scope)
        db.commit()
    return RedirectResponse(url=redirect_to, status_code=302)


@router.get("/life", response_class=HTMLResponse)
def life_view(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    life = _get_life_scope(db)
    return templates.TemplateResponse("life.html", _ctx(request, life=life))


# --- Categories CRUD ---

@router.get("/categories", response_class=HTMLResponse)
def categories_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(db.scalars(select(Category).order_by(Category.sort_order, Category.name)))
    return templates.TemplateResponse("categories.html", _ctx(request, categories=items))


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
    _get_life_scope(db)  # ensure singleton exists
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
    return templates.TemplateResponse(
        "upload.html", _ctx(request, scopes=scopes, categories=categories)
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    scope_id: int = Form(...),
    category_id: int = Form(...),
    year: int | None = Form(None),
    file: UploadFile = ...,
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    scope = db.get(Scope, scope_id)
    cat = db.get(Category, category_id)
    if not scope or not cat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown scope or category")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    digest = sha256_bytes(data)
    existing = db.scalar(select(ProcessedDocument).where(ProcessedDocument.sha256 == digest))
    if existing:
        return templates.TemplateResponse(
            "upload_result.html",
            _ctx(request, duplicate=True, doc=existing, link=None),
        )

    path = build_path(
        scope_kind=scope.kind,
        scope_name=scope.name,
        category_name=cat.name,
        year=year,
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
        year=year or datetime.utcnow().year,
        drive_file_id=result.file_id,
        drive_path=result.drive_path,
        confidence=1.0,
        status=DocStatus.filed,
        filed_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return templates.TemplateResponse(
        "upload_result.html",
        _ctx(request, doc=doc, link=result.web_view_link, duplicate=False),
    )
