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

    `DRIVE_OAUTH_TOKEN_JSON` can be either:
      - a path to a JSON file produced by `google-auth-oauthlib` (local dev), or
      - the JSON document itself (Cloud Run + Secret Manager mounts the secret
        contents directly into the env var when deployed with
        `--set-secrets=DRIVE_OAUTH_TOKEN_JSON=drive-token:latest`).
    """
    settings = get_settings()
    raw = settings.drive_oauth_token_json
    if not raw:
        raise RuntimeError(
            "DRIVE_OAUTH_TOKEN_JSON is not set; cannot upload to Drive."
        )
    info = _resolve_token_payload(raw)
    return Credentials.from_authorized_user_info(info, scopes=DRIVE_SCOPES)


def _resolve_token_payload(value: str) -> dict:
    stripped = value.strip()
    if stripped.startswith("{"):
        # Secret Manager mounts the JSON directly into the env var.
        return json.loads(stripped)
    p = Path(stripped)
    if not p.exists():
        raise FileNotFoundError(f"Drive token file not found at {p}")
    return json.loads(p.read_text())
