"""CRUD for the PDF password vault."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.config import get_settings, is_local_mode
from app.db import get_db
from app.models import PdfPassword

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


@router.get("/passwords", response_class=HTMLResponse)
def passwords_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    items = list(
        db.scalars(
            select(PdfPassword).order_by(PdfPassword.priority.asc(), PdfPassword.id.asc())
        )
    )
    return templates.TemplateResponse("passwords.html", _ctx(request, items=items))


@router.post("/passwords")
def passwords_create(
    label: str = Form(...),
    password: str = Form(...),
    sender_match: str = Form(""),
    filename_match: str = Form(""),
    priority: int = Form(100),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    label = label.strip()
    password = password.strip()
    if not label or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Label and password are required")
    db.add(
        PdfPassword(
            label=label,
            password=password,
            sender_match=sender_match.strip() or None,
            filename_match=filename_match.strip() or None,
            priority=priority,
        )
    )
    db.commit()
    return RedirectResponse(url="/passwords", status_code=302)


@router.post("/passwords/{password_id}/delete")
def passwords_delete(
    password_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    entry = db.get(PdfPassword, password_id)
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse(url="/passwords", status_code=302)
