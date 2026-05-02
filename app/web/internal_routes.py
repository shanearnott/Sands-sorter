"""Internal endpoints called by Cloud Scheduler (or `python -m app.worker`).

Auth: a shared secret in the `X-Internal-Key` header. The same value is
stored in Secret Manager (`internal-api-key`), mounted into the Cloud Run
container as `INTERNAL_API_KEY`, and passed to Cloud Scheduler as a header.
If `INTERNAL_API_KEY` is unset (e.g. local dev), the endpoints fall back
to requiring the user-session allowlist.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.auth import current_user
from app.config import get_settings
from app.db import get_db
from app.summary.digest import build_and_emit
from app.worker.tasks import poll_all

logger = logging.getLogger(__name__)
router = APIRouter()


def require_internal(
    request: Request,
    x_internal_key: str | None = Header(default=None),
) -> str:
    """Accept either a matching `X-Internal-Key` (Cloud Scheduler) or a
    logged-in allowlisted user (manual trigger from a browser tab)."""
    settings = get_settings()
    expected = settings.internal_api_key
    if expected and x_internal_key and hmac.compare_digest(x_internal_key, expected):
        return "scheduler"
    # Fall back to the human-user check.
    user = current_user(request)
    return user


@router.post("/internal/poll")
def internal_poll(
    db: Session = Depends(get_db),
    _: str = Depends(require_internal),
):
    summary = poll_all(db)
    return summary.__dict__


@router.post("/internal/summary")
def internal_summary(
    cadence: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(require_internal),
):
    return build_and_emit(db, cadence=cadence)
