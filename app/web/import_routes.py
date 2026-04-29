"""Routes for the bulk-import wizard, rules CRUD, and the /moves dashboard.

Split out of `routes.py` so that file stays focused on M1 surfaces.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, optional_user
from app.classifier.llm import ClaudeFallback
from app.config import get_settings
from app.db import get_db
from app.drive.credentials import load_drive_credentials
from app.drive.uploader import DriveUploader
from app.extraction.invoice import InvoiceExtractor
from app.importer import wizard
from app.importer.sources import DriveTreeSource, LocalTreeSource
from app.models import (
    Category,
    Direction,
    ImportItem,
    ImportItemStatus,
    ImportJob,
    ImportJobStatus,
    ImportSourceKind,
    MatchType,
    ProcessedDocument,
    Rule,
    RuleSource,
    Scope,
    ScopeKind,
)

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


# --- Import wizard ---------------------------------------------------------

@router.get("/import", response_class=HTMLResponse)
def import_index(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    jobs = list(
        db.scalars(select(ImportJob).order_by(ImportJob.created_at.desc()).limit(20))
    )
    return templates.TemplateResponse("import_index.html", _ctx(request, jobs=jobs))


@router.post("/import")
def import_create(
    source_kind: str = Form(...),
    source_ref: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    try:
        kind = ImportSourceKind(source_kind)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown source kind") from exc

    source_ref = source_ref.strip()
    if not source_ref:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source_ref is required")

    try:
        source = _build_source(kind, source_ref)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    job = ImportJob(
        source_kind=kind,
        source_ref=source_ref,
        status=ImportJobStatus.running,
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    wizard.enqueue_items(db, job, source)
    return RedirectResponse(url=f"/import/{job.id}", status_code=302)


@router.get("/import/{job_id}", response_class=HTMLResponse)
def import_show(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    job = db.get(ImportJob, job_id) or _not_found("Import job not found")

    pending = db.scalar(
        select(ImportItem.id)
        .where(ImportItem.job_id == job.id)
        .where(ImportItem.status == ImportItemStatus.pending)
        .limit(1)
    )
    awaiting = list(
        db.scalars(
            select(ImportItem)
            .where(ImportItem.job_id == job.id)
            .where(ImportItem.status == ImportItemStatus.awaiting)
            .order_by(ImportItem.id)
            .limit(50)
        )
    )
    scopes = list(db.scalars(select(Scope).where(Scope.active.is_(True)).order_by(Scope.kind, Scope.name)))
    categories = list(db.scalars(select(Category).order_by(Category.name)))

    return templates.TemplateResponse(
        "import_show.html",
        _ctx(
            request,
            job=job,
            has_pending=pending is not None,
            awaiting=awaiting,
            scopes=scopes,
            categories=categories,
            directions=list(Direction),
        ),
    )


@router.post("/import/{job_id}/process")
def import_process(
    job_id: int,
    batch_size: int = Form(20),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    job = db.get(ImportJob, job_id) or _not_found("Import job not found")

    extractor = _maybe_extractor()
    llm = _maybe_llm()
    uploader = _maybe_uploader()

    pending = list(
        db.scalars(
            select(ImportItem)
            .where(ImportItem.job_id == job.id)
            .where(ImportItem.status == ImportItemStatus.pending)
            .order_by(ImportItem.id)
            .limit(batch_size)
        )
    )
    for item in pending:
        wizard.process_item(
            db,
            item,
            fetch_bytes=lambda i, _job=job: _fetch_bytes(_job, i),
            extractor=extractor,
            llm=llm,
            uploader=uploader,
        )
    wizard.mark_done_if_drained(db, job)
    return RedirectResponse(url=f"/import/{job.id}", status_code=302)


@router.post("/import/{job_id}/items/{item_id}/decide")
def import_decide(
    job_id: int,
    item_id: int,
    scope_id: int = Form(...),
    category_id: int = Form(...),
    direction: Direction = Form(Direction.expense),
    save_as_rule: bool = Form(False),
    rule_pattern: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    job = db.get(ImportJob, job_id) or _not_found("Import job not found")
    item = db.get(ImportItem, item_id) or _not_found("Import item not found")
    if item.job_id != job.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Item belongs to a different job")

    uploader = _maybe_uploader()
    if uploader is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Drive uploader not configured (set DRIVE_OAUTH_TOKEN_JSON + DRIVE_ROOT_FOLDER_ID).",
        )

    wizard.apply_decision(
        db,
        item,
        fetch_bytes=lambda i, _job=job: _fetch_bytes(_job, i),
        uploader=uploader,
        scope_id=scope_id,
        category_id=category_id,
        direction=direction,
        save_as_rule=bool(save_as_rule),
        rule_pattern=rule_pattern.strip() or None,
    )
    wizard.mark_done_if_drained(db, job)
    return RedirectResponse(url=f"/import/{job.id}", status_code=302)


# --- Rules CRUD ------------------------------------------------------------

@router.get("/rules", response_class=HTMLResponse)
def rules_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    rules = list(
        db.scalars(select(Rule).order_by(Rule.priority.asc(), Rule.id.asc()))
    )
    scopes = list(db.scalars(select(Scope).where(Scope.active.is_(True)).order_by(Scope.kind, Scope.name)))
    categories = list(db.scalars(select(Category).order_by(Category.name)))
    return templates.TemplateResponse(
        "rules.html",
        _ctx(
            request,
            rules=rules,
            scopes=scopes,
            categories=categories,
            match_types=list(MatchType),
            directions=list(Direction),
        ),
    )


@router.post("/rules")
def rules_create(
    match_type: MatchType = Form(...),
    pattern: str = Form(...),
    scope_id: int = Form(...),
    category_id: int = Form(...),
    direction: Direction | None = Form(None),
    priority: int = Form(100),
    confidence: float = Form(0.95),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    pattern = pattern.strip()
    if not pattern:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pattern is required")
    db.add(
        Rule(
            match_type=match_type,
            pattern=pattern,
            scope_id=scope_id,
            category_id=category_id,
            direction=direction,
            priority=priority,
            confidence=confidence,
            source=RuleSource.manual,
        )
    )
    db.commit()
    return RedirectResponse(url="/rules", status_code=302)


@router.post("/rules/{rule_id}/delete")
def rules_delete(
    rule_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    rule = db.get(Rule, rule_id)
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse(url="/rules", status_code=302)


@router.post("/rules/{rule_id}/toggle")
def rules_toggle(
    rule_id: int, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    rule = db.get(Rule, rule_id)
    if rule:
        rule.enabled = not rule.enabled
        db.commit()
    return RedirectResponse(url="/rules", status_code=302)


# --- /moves (recent processed documents) ----------------------------------

@router.get("/moves", response_class=HTMLResponse)
def moves_list(
    request: Request, db: Session = Depends(get_db), _: str = Depends(current_user)
):
    docs = list(
        db.scalars(
            select(ProcessedDocument)
            .order_by(ProcessedDocument.created_at.desc())
            .limit(100)
        )
    )
    scope_lookup = {s.id: s for s in db.scalars(select(Scope))}
    cat_lookup = {c.id: c for c in db.scalars(select(Category))}
    return templates.TemplateResponse(
        "moves.html",
        _ctx(request, docs=docs, scopes=scope_lookup, categories=cat_lookup),
    )


# --- Helpers --------------------------------------------------------------

def _build_source(kind: ImportSourceKind, source_ref: str):
    if kind == ImportSourceKind.local:
        return LocalTreeSource(source_ref)
    if kind == ImportSourceKind.drive:
        from googleapiclient.discovery import build

        creds = load_drive_credentials()
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return DriveTreeSource(service, source_ref)
    raise RuntimeError(f"Unknown source kind: {kind}")


def _fetch_bytes(job: ImportJob, item: ImportItem) -> bytes:
    if job.source_kind == ImportSourceKind.local:
        path = Path(job.source_ref) / item.source_path
        return path.read_bytes()
    if job.source_kind == ImportSourceKind.drive:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        import io

        if not item.source_file_id:
            raise RuntimeError(f"ImportItem {item.id} has no Drive file_id stored")
        creds = load_drive_credentials()
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        request = service.files().get_media(fileId=item.source_file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        return buffer.getvalue()
    raise RuntimeError(f"Cannot fetch bytes for source kind {job.source_kind}")


def _maybe_extractor() -> InvoiceExtractor | None:
    settings = get_settings()
    if not (settings.gcp_project_id and settings.document_ai_processor_id):
        return None
    try:
        return InvoiceExtractor()
    except Exception as exc:  # noqa: BLE001 - graceful degradation
        logger.warning("Document AI not available: %s", exc)
        return None


def _maybe_llm() -> ClaudeFallback | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    try:
        return ClaudeFallback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic LLM not available: %s", exc)
        return None


def _maybe_uploader() -> DriveUploader | None:
    settings = get_settings()
    if not (settings.drive_root_folder_id and settings.drive_oauth_token_json):
        return None
    try:
        creds = load_drive_credentials()
        return DriveUploader(creds, settings.drive_root_folder_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drive uploader not available: %s", exc)
        return None


def _not_found(message: str):
    raise HTTPException(status.HTTP_404_NOT_FOUND, message)
