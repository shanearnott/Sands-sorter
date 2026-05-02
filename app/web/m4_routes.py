"""M4 surfaces: vendors CRUD, sources health, reassignment, unsorted queue."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.config import get_settings
from app.db import get_db
from app.models import (
    Category,
    Direction,
    DocStatus,
    MatchType,
    PERSONAL_SCOPE_NAME,
    ProcessedDocument,
    Reassignment,
    Rule,
    RuleSource,
    Scope,
    Source,
    SourceKind,
    Vendor,
)

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


# --- Vendors --------------------------------------------------------------

@router.get("/vendors", response_class=HTMLResponse)
def vendors_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(db.scalars(select(Vendor).order_by(Vendor.name)))
    scopes = list(db.scalars(select(Scope).where(Scope.active.is_(True)).order_by(Scope.kind, Scope.name)))
    categories = list(db.scalars(select(Category).order_by(Category.name)))
    return templates.TemplateResponse(
        "vendors.html",
        _ctx(
            request,
            vendors=items,
            scopes=scopes,
            categories=categories,
            directions=list(Direction),
        ),
    )


@router.post("/vendors")
def vendors_create(
    name: str = Form(...),
    default_scope_id: int | None = Form(None),
    default_category_id: int | None = Form(None),
    default_direction: Direction | None = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is required")
    db.add(
        Vendor(
            name=name,
            default_scope_id=default_scope_id or None,
            default_category_id=default_category_id or None,
            default_direction=default_direction,
            notes=notes.strip() or None,
        )
    )
    db.commit()
    return RedirectResponse(url="/vendors", status_code=302)


@router.post("/vendors/{vendor_id}/delete")
def vendors_delete(
    vendor_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    vendor = db.get(Vendor, vendor_id)
    if vendor:
        db.delete(vendor)
        db.commit()
    return RedirectResponse(url="/vendors", status_code=302)


# --- Sources --------------------------------------------------------------

@router.get("/sources", response_class=HTMLResponse)
def sources_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(db.scalars(select(Source).order_by(Source.kind, Source.label)))
    settings = get_settings()
    return templates.TemplateResponse(
        "sources.html",
        _ctx(
            request,
            sources=items,
            kinds=list(SourceKind),
            demo_mailbox=bool(settings.demo_mailbox_root),
            demo_dropbox=bool(settings.demo_dropbox_root),
        ),
    )


@router.post("/sources")
def sources_create(
    kind: SourceKind = Form(...),
    label: str = Form(...),
    oauth_secret_name: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    label = label.strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Label is required")
    db.add(
        Source(
            kind=kind,
            label=label,
            oauth_secret_name=oauth_secret_name.strip() or None,
            enabled=True,
        )
    )
    db.commit()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/allowlist")
def sources_update_allowlist(
    source_id: int,
    body_allowlist: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    senders = [
        line.strip()
        for line in body_allowlist.replace(",", "\n").splitlines()
        if line.strip()
    ]
    source.body_allowlist = senders or None
    db.commit()
    return RedirectResponse(url="/sources", status_code=302)


@router.post("/sources/{source_id}/toggle")
def sources_toggle(
    source_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    source = db.get(Source, source_id)
    if source:
        source.enabled = not source.enabled
        db.commit()
    return RedirectResponse(url="/sources", status_code=302)


# --- Moves reassignment + Unsorted queue ----------------------------------

@router.get("/unsorted", response_class=HTMLResponse)
def unsorted_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    docs = list(
        db.scalars(
            select(ProcessedDocument)
            .where(ProcessedDocument.status == DocStatus.unsorted)
            .order_by(ProcessedDocument.created_at.desc())
            .limit(100)
        )
    )
    scopes = list(db.scalars(select(Scope).where(Scope.active.is_(True)).order_by(Scope.kind, Scope.name)))
    categories = list(db.scalars(select(Category).order_by(Category.name)))
    return templates.TemplateResponse(
        "unsorted.html",
        _ctx(
            request,
            docs=docs,
            scopes=scopes,
            categories=categories,
            directions=list(Direction),
        ),
    )


@router.post("/moves/{doc_id}/reassign")
def moves_reassign(
    doc_id: int,
    scope_id: int = Form(...),
    category_id: int = Form(...),
    direction: Direction = Form(Direction.expense),
    save_as_rule: bool = Form(False),
    rule_pattern: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    doc = db.get(ProcessedDocument, doc_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    scope = db.get(Scope, scope_id)
    cat = db.get(Category, category_id)
    if not scope or not cat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown scope or category")

    db.add(
        Reassignment(
            document_id=doc.id,
            old_scope_id=doc.scope_id,
            old_category_id=doc.category_id,
            old_direction=doc.direction,
            new_scope_id=scope.id,
            new_category_id=cat.id,
            new_direction=direction,
        )
    )

    learned: Rule | None = None
    if save_as_rule and rule_pattern.strip():
        learned = Rule(
            match_type=MatchType.contains,
            pattern=rule_pattern.strip(),
            scope_id=scope.id,
            category_id=cat.id,
            direction=direction,
            priority=50,
            confidence=0.9,
            source=RuleSource.learned_from_reassign,
        )
        db.add(learned)

    doc.scope_id = scope.id
    doc.category_id = cat.id
    doc.direction = direction
    doc.status = DocStatus.filed
    doc.classifier = doc.classifier  # unchanged — keeps the original signal source
    doc.filed_at = doc.filed_at or datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/moves", status_code=302)
