from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "prod"] = "local"
    secret_key: str = "change-me"
    timezone: str = "Australia/Sydney"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://sands:sands@localhost:5432/sands"

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_url: str = "http://localhost:8000/auth/callback"
    allowed_emails: str = ""

    dropbox_app_key: str = ""
    dropbox_app_secret: str = ""
    dropbox_refresh_token: str = ""
    dropbox_inbox_path: str = "/Inbox"
    dropbox_processed_path: str = "/processed"

    drive_root_folder_id: str = ""
    drive_oauth_token_json: str = ""

    gcp_project_id: str = ""
    document_ai_processor_id: str = ""
    document_ai_location: str = "us"
    secret_manager_prefix: str = "sands"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    summary_cadence: Literal["weekly", "daily"] = "weekly"
    summary_hour: int = 7
    summary_recipients: str = ""
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    ocr_text_retention_days: int = 90

    # Shared secret used by Cloud Scheduler (X-Internal-Key header) to invoke
    # /internal/poll and /internal/summary.
    internal_api_key: str = ""

    # Demo / no-credentials mode. When set, M3 falls back to local stand-ins.
    demo_drive_root: str = ""           # writes files here instead of Drive
    demo_dropbox_root: str = ""         # poller watches this folder
    demo_mailbox_root: str = ""         # poller reads .eml files from here
    demo_digest_dir: str = ""           # writes summary HTML here
    demo_ocr: bool = False              # forces local OCR even if Document AI is set

    @property
    def allowed_emails_list(self) -> list[str]:
        return [e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()]

    @property
    def summary_recipients_list(self) -> list[str]:
        return [e.strip() for e in self.summary_recipients.split(",") if e.strip()]

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
