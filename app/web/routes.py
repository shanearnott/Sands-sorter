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
    Category,
    Classifier,
    DocStatus,
    ProcessedDocument,
    Property,
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


# --- Properties CRUD ---

@router.get("/properties", response_class=HTMLResponse)
def properties_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(db.scalars(select(Property).order_by(Property.name)))
    return templates.TemplateResponse("properties.html", _ctx(request, properties=items))


@router.post("/properties", response_class=HTMLResponse)
def properties_create(
    name: str = Form(...),
    drive_folder_id: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is required")
    p = Property(name=name, drive_folder_id=drive_folder_id.strip() or None)
    db.add(p)
    db.commit()
    return RedirectResponse(url="/properties", status_code=302)


@router.post("/properties/{prop_id}/delete")
def properties_delete(
    prop_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    p = db.get(Property, prop_id)
    if p:
        db.delete(p)
        db.commit()
    return RedirectResponse(url="/properties", status_code=302)


# --- Categories CRUD ---

@router.get("/categories", response_class=HTMLResponse)
def categories_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(db.scalars(select(Category).order_by(Category.sort_order, Category.name)))
    return templates.TemplateResponse("categories.html", _ctx(request, categories=items))


@router.post("/categories", response_class=HTMLResponse)
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


# --- Manual upload (M1 happy path: pick property + category, file goes to Drive) ---

@router.get("/upload", response_class=HTMLResponse)
def upload_form(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    properties = list(db.scalars(select(Property).where(Property.active.is_(True)).order_by(Property.name)))
    categories = list(db.scalars(select(Category).order_by(Category.sort_order, Category.name)))
    return templates.TemplateResponse(
        "upload.html",
        _ctx(request, properties=properties, categories=categories),
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    property_id: int = Form(...),
    category_id: int = Form(...),
    year: int | None = Form(None),
    file: UploadFile = ...,
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    prop = db.get(Property, property_id)
    cat = db.get(Category, category_id)
    if not prop or not cat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown property or category")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    digest = sha256_bytes(data)
    existing = db.scalar(select(ProcessedDocument).where(ProcessedDocument.sha256 == digest))
    if existing:
        return templates.TemplateResponse(
            "upload_result.html",
            _ctx(
                request,
                duplicate=True,
                doc=existing,
            ),
        )

    path = build_path(
        property_name=prop.name,
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
        property_id=prop.id,
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
