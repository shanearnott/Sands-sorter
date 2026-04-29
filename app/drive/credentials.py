from __future__ import annotations

import json
import logging
from pathlib import Path

from google.oauth2.credentials import Credentials

from app.config import get_settings

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def load_drive_credentials() -> Credentials:
    """Load OAuth credentials for Drive uploads.

    Local dev: reads JSON from `DRIVE_OAUTH_TOKEN_JSON` (a path produced by
    `google-auth-oauthlib` flow). In M3 this is replaced by a Secret Manager loader.
    """
    settings = get_settings()
    token_path = settings.drive_oauth_token_json
    if not token_path:
        raise RuntimeError(
            "DRIVE_OAUTH_TOKEN_JSON is not set; cannot upload to Drive."
        )
    p = Path(token_path)
    if not p.exists():
        raise FileNotFoundError(f"Drive token file not found at {p}")
    info = json.loads(p.read_text())
    creds = Credentials.from_authorized_user_info(info, scopes=DRIVE_SCOPES)
    return creds
