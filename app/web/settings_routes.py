"""Runtime-editable settings page.

Backed by the `app_config` key/value table. Saving here flips the app between
local-only mode and Google-authed mode without restarting.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth import optional_user
from app.config import (
    RUNTIME_OVERLAY_KEYS,
    get_settings,
    is_local_mode,
    set_runtime_overrides,
)
from app.db import get_db
from app.models import AppConfig

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# Display order on the page, grouped semantically.
SETTINGS_GROUPS: list[tuple[str, list[tuple[str, str, str, bool]]]] = [
    ("Google sign-in", [
        ("google_oauth_client_id", "OAuth client ID",
         "From Google Cloud Console → Credentials → OAuth 2.0 Client IDs.", False),
        ("google_oauth_client_secret", "OAuth client secret",
         "Paired with the client ID.", True),
        ("google_oauth_redirect_url", "OAuth redirect URL",
         "Default works for local dev.", False),
        ("allowed_emails", "Allowed emails",
         "Comma-separated allowlist of Google accounts that may sign in.", False),
    ]),
    ("Google Drive", [
        ("drive_root_folder_id", "Drive root folder ID",
         "Parent folder in Drive under which Properties/, Personal/, etc. live.", False),
    ]),
    ("Gmail / Dropbox sources", [
        ("dropbox_app_key", "Dropbox app key", "", False),
        ("dropbox_app_secret", "Dropbox app secret", "", True),
    ]),
    ("LLM fallback", [
        ("anthropic_api_key", "Anthropic API key",
         "Enables Claude fallback when rules don't match.", True),
    ]),
]


def _load_db_values(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.scalars(select(AppConfig))}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    db_values = _load_db_values(db)
    rows = []
    for group_title, fields in SETTINGS_GROUPS:
        group_rows = []
        for key, label, hint, is_secret in fields:
            env_value = getattr(settings, key, "") or ""
            db_value = db_values.get(key, "")
            effective = db_value or env_value
            group_rows.append({
                "key": key,
                "label": label,
                "hint": hint,
                "is_secret": is_secret,
                "value": db_value,           # what's currently saved in app_config
                "env_value": env_value,      # placeholder hint for the user
                "effective": effective,      # what the app actually uses
                "from_env": bool(env_value and not db_value),
            })
        rows.append((group_title, group_rows))

    saved = request.query_params.get("saved") == "1"
    ctx = {
        "request": request,
        "user_email": optional_user(request),
        "settings": settings,
        "local_mode": is_local_mode(),
        "groups": rows,
        "saved": saved,
    }
    return templates.TemplateResponse("settings.html", ctx)


@router.post("/settings")
async def settings_save(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    incoming: dict[str, str] = {}
    for key in RUNTIME_OVERLAY_KEYS:
        if key in form:
            incoming[key] = (form.get(key) or "").strip()

    for key, value in incoming.items():
        stmt = pg_insert(AppConfig).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=[AppConfig.key],
            set_={"value": value},
        )
        db.execute(stmt)
    db.commit()

    # Refresh in-memory overlay so the change takes effect immediately.
    set_runtime_overrides(_load_db_values(db))

    return RedirectResponse(url="/settings?saved=1", status_code=303)
