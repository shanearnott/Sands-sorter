"""Internal endpoints called by Cloud Scheduler (or `python -m app.worker`).
Authentication is by OIDC in prod; in demo mode we just require the
allowlist. Both endpoints are also reachable via the worker CLI."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.summary.digest import build_and_emit
from app.worker.tasks import poll_all

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/internal/poll")
def internal_poll(
    db: Session = Depends(get_db), _: str = Depends(current_user)
):
    summary = poll_all(db)
    return summary.__dict__


@router.post("/internal/summary")
def internal_summary(
    cadence: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: str = Depends(current_user),
):
    return build_and_emit(db, cadence=cadence)
