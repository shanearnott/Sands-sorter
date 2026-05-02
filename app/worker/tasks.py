"""Worker tasks: `poll_all` iterates configured sources and runs every
discovered document through the shared pipeline; `summarize` builds the
income/expenses/net digest for the period.

Pollers are selected based on credential availability (live vs demo). A
source whose creds are missing is silently skipped.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classifier.llm import ClaudeFallback
from app.config import get_settings
from app.models import (
    DocStatus,
    Source,
    SourceKind,
)
from app.pipeline import process_raw_doc
from app.pollers.base import Poller, RawDoc

logger = logging.getLogger(__name__)


@dataclass
class PollSummary:
    sources_polled: int = 0
    sources_skipped: int = 0
    docs_filed: int = 0
    docs_unsorted: int = 0
    docs_error: int = 0
    docs_skipped_dup: int = 0


def poll_all(db: Session) -> PollSummary:
    """Poll every enabled `Source`. For each, instantiate a live or demo
    poller, drain it, and run every yielded `RawDoc` through the pipeline."""
    settings = get_settings()
    summary = PollSummary()

    llm = _maybe_llm()

    sources = list(db.scalars(select(Source).where(Source.enabled.is_(True))))
    if not sources:
        # Demo convenience: if no sources are configured but a demo path is,
        # auto-create one.
        sources = list(_seed_demo_sources(db))

    for source in sources:
        poller = _build_poller(db, source)
        if poller is None:
            summary.sources_skipped += 1
            logger.info("Source %s: no credentials, skipping", source.label)
            continue
        summary.sources_polled += 1
        for raw in poller.fetch():
            result = process_raw_doc(db, raw, llm=llm)
            if result.status == DocStatus.filed:
                summary.docs_filed += 1
                if result.reason.startswith("duplicate"):
                    summary.docs_skipped_dup += 1
                else:
                    poller.mark_processed(raw)
            elif result.status == DocStatus.unsorted:
                summary.docs_unsorted += 1
                poller.mark_processed(raw)
            else:
                summary.docs_error += 1
    return summary


def _build_poller(db: Session, source: Source) -> Poller | None:
    settings = get_settings()
    if source.kind == SourceKind.gmail:
        if settings.demo_mailbox_root:
            from app.pollers.local_mailbox import LocalMailboxPoller
            return LocalMailboxPoller(db, source, settings.demo_mailbox_root)
        if source.oauth_secret_name:
            from app.pollers.gmail import GmailPoller
            return GmailPoller(db, source, source.oauth_secret_name)
        return None

    if source.kind == SourceKind.dropbox:
        if settings.demo_dropbox_root:
            from app.pollers.local_dropbox import LocalDropboxPoller
            return LocalDropboxPoller(db, source, settings.demo_dropbox_root)
        if settings.dropbox_refresh_token:
            from app.pollers.dropbox import DropboxPoller
            access_token = _exchange_dropbox_refresh(settings)
            if access_token is None:
                return None
            return DropboxPoller(db, source, access_token, settings.dropbox_inbox_path)
        return None

    return None


def _seed_demo_sources(db: Session) -> Iterable[Source]:
    """Auto-create a demo Gmail + Dropbox source on first poll if the user
    has set DEMO_* env vars but no sources exist yet."""
    settings = get_settings()
    if settings.demo_dropbox_root:
        existing = db.scalar(
            select(Source).where(Source.kind == SourceKind.dropbox).where(Source.label == "demo")
        )
        if not existing:
            existing = Source(kind=SourceKind.dropbox, label="demo", enabled=True)
            db.add(existing)
            db.commit()
        yield existing
    if settings.demo_mailbox_root:
        existing = db.scalar(
            select(Source).where(Source.kind == SourceKind.gmail).where(Source.label == "demo")
        )
        if not existing:
            existing = Source(kind=SourceKind.gmail, label="demo", enabled=True)
            db.add(existing)
            db.commit()
        yield existing


def _maybe_llm() -> ClaudeFallback | None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    try:
        return ClaudeFallback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic LLM unavailable: %s", exc)
        return None


def _exchange_dropbox_refresh(settings) -> str | None:
    try:
        import requests  # type: ignore
    except ImportError:
        try:
            import httpx as requests  # type: ignore
        except ImportError:
            logger.warning("No HTTP client available for Dropbox token refresh")
            return None
    try:
        resp = requests.post(
            "https://api.dropboxapi.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.dropbox_refresh_token,
                "client_id": settings.dropbox_app_key,
                "client_secret": settings.dropbox_app_secret,
            },
            timeout=15,
        )
        return resp.json().get("access_token")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dropbox token refresh failed: %s", exc)
        return None
