from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScopeKind(str, enum.Enum):
    property = "property"
    personal = "personal"
    entity = "entity"


class ScopeCountry(str, enum.Enum):
    AU = "AU"
    US = "US"


class Direction(str, enum.Enum):
    expense = "expense"
    income = "income"


class SourceKind(str, enum.Enum):
    gmail = "gmail"
    dropbox = "dropbox"


class MatchType(str, enum.Enum):
    contains = "contains"
    regex = "regex"
    sender_email = "sender_email"
    filename = "filename"


class RuleSource(str, enum.Enum):
    manual = "manual"
    learned_from_reassign = "learned_from_reassign"


class Classifier(str, enum.Enum):
    rule = "rule"
    llm = "llm"
    manual = "manual"
    unsorted = "unsorted"


class DocStatus(str, enum.Enum):
    filed = "filed"
    unsorted = "unsorted"
    error = "error"


# Shared SQL ENUM type instance for `Direction` so the Postgres type is created
# once and reused across the many columns that reference it.
_direction_enum = Enum(Direction, name="direction")


class Scope(Base, TimestampMixin):
    """A top-level filing target. Property (houses + cars), the singleton
    Personal bucket, or an Entity (trust or company). Each scope has a country
    which determines its financial-year start month (AU rolls 1 July, US is
    calendar)."""

    __tablename__ = "scopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ScopeKind] = mapped_column(Enum(ScopeKind, name="scope_kind"))
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[ScopeCountry] = mapped_column(
        Enum(ScopeCountry, name="scope_country"), default=ScopeCountry.AU, nullable=False
    )
    drive_folder_id: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    rules: Mapped[list["Rule"]] = relationship(back_populates="scope")

    __table_args__ = (UniqueConstraint("kind", "name", name="uq_scope_kind_name"),)


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_direction: Mapped[Direction | None] = mapped_column(
        _direction_enum
    )

    rules: Mapped[list["Rule"]] = relationship(back_populates="category")


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    default_scope_id: Mapped[int | None] = mapped_column(ForeignKey("scopes.id"))
    default_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    default_direction: Mapped[Direction | None] = mapped_column(
        _direction_enum
    )
    notes: Mapped[str | None] = mapped_column(Text)


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_type: Mapped[MatchType] = mapped_column(Enum(MatchType, name="match_type"))
    pattern: Mapped[str] = mapped_column(String(500))
    scope_id: Mapped[int] = mapped_column(ForeignKey("scopes.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    direction: Mapped[Direction | None] = mapped_column(_direction_enum)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[RuleSource] = mapped_column(
        Enum(RuleSource, name="rule_source"), default=RuleSource.manual, nullable=False
    )

    scope: Mapped[Scope] = relationship(back_populates="rules")
    category: Mapped[Category] = relationship(back_populates="rules")


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind, name="source_kind"))
    label: Mapped[str] = mapped_column(String(120))
    oauth_secret_name: Mapped[str | None] = mapped_column(String(200))
    cursor: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    body_allowlist: Mapped[list[str] | None] = mapped_column(JSON)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("kind", "label", name="uq_source_kind_label"),)


class ProcessedDocument(Base, TimestampMixin):
    __tablename__ = "processed_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    source_msg_ref: Mapped[str | None] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(500))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    classifier: Mapped[Classifier] = mapped_column(Enum(Classifier, name="classifier"))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"))
    scope_id: Mapped[int | None] = mapped_column(ForeignKey("scopes.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    direction: Mapped[Direction] = mapped_column(
        _direction_enum, default=Direction.expense, nullable=False
    )
    doc_date: Mapped[date | None] = mapped_column(Date)
    financial_year: Mapped[int | None] = mapped_column(Integer)
    drive_file_id: Mapped[str | None] = mapped_column(String(120))
    drive_path: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[DocStatus] = mapped_column(Enum(DocStatus, name="doc_status"))
    error: Mapped[str | None] = mapped_column(Text)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extraction: Mapped["DocumentExtraction | None"] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )


class DocumentExtraction(Base, TimestampMixin):
    """Structured fields pulled out by Document AI Invoice Parser. 1:1 with
    `processed_documents`. Powers the M5 dashboard."""

    __tablename__ = "document_extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("processed_documents.id"), unique=True
    )
    counterparty_text: Mapped[str | None] = mapped_column(String(300))
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    doc_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    account_number: Mapped[str | None] = mapped_column(String(120))
    raw_json: Mapped[dict | None] = mapped_column(JSON)

    document: Mapped[ProcessedDocument] = relationship(back_populates="extraction")


class Reassignment(Base, TimestampMixin):
    __tablename__ = "reassignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("processed_documents.id"))
    old_scope_id: Mapped[int | None] = mapped_column(ForeignKey("scopes.id"))
    old_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    old_direction: Mapped[Direction | None] = mapped_column(_direction_enum)
    new_scope_id: Mapped[int] = mapped_column(ForeignKey("scopes.id"))
    new_category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    new_direction: Mapped[Direction | None] = mapped_column(_direction_enum)
    created_rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"))


class ImportJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    done = "done"
    cancelled = "cancelled"
    error = "error"


class ImportItemStatus(str, enum.Enum):
    pending = "pending"     # discovered, not yet classified
    awaiting = "awaiting"   # classified but needs user decision
    copied = "copied"       # filed in Drive
    skipped = "skipped"     # duplicate sha256, already filed
    error = "error"


class ImportSourceKind(str, enum.Enum):
    drive = "drive"
    local = "local"


class ImportJob(Base, TimestampMixin):
    """A bulk-import run. Tracks progress over a tree of source files."""

    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_kind: Mapped[ImportSourceKind] = mapped_column(
        Enum(ImportSourceKind, name="import_source_kind")
    )
    source_ref: Mapped[str] = mapped_column(String(500))   # drive folder id, or local path
    status: Mapped[ImportJobStatus] = mapped_column(
        Enum(ImportJobStatus, name="import_job_status"),
        default=ImportJobStatus.pending,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    copied_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    awaiting_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ImportItem(Base, TimestampMixin):
    __tablename__ = "import_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"))
    source_path: Mapped[str] = mapped_column(String(1000))      # relative within the job's tree
    source_file_id: Mapped[str | None] = mapped_column(String(120))  # Drive file id when applicable
    mime_type: Mapped[str | None] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    extracted_amount_cents: Mapped[int | None] = mapped_column(Integer)
    extracted_currency: Mapped[str | None] = mapped_column(String(3))
    extracted_doc_date: Mapped["date | None"] = mapped_column(Date)
    extracted_due_date: Mapped["date | None"] = mapped_column(Date)
    status: Mapped[ImportItemStatus] = mapped_column(
        Enum(ImportItemStatus, name="import_item_status"),
        default=ImportItemStatus.pending,
        nullable=False,
    )

    # classifier proposal
    proposed_scope_id: Mapped[int | None] = mapped_column(ForeignKey("scopes.id"))
    proposed_scope_name: Mapped[str | None] = mapped_column(String(120))
    proposed_scope_kind: Mapped[ScopeKind | None] = mapped_column(
        Enum(ScopeKind, name="scope_kind")
    )
    proposed_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    proposed_category_name: Mapped[str | None] = mapped_column(String(120))
    proposed_direction: Mapped[Direction | None] = mapped_column(_direction_enum)
    proposed_fy: Mapped[int | None] = mapped_column(Integer)
    proposed_drive_path: Mapped[str | None] = mapped_column(String(1000))
    counterparty: Mapped[str | None] = mapped_column(String(300))
    confidence: Mapped[float | None] = mapped_column(Float)
    llm_reasoning: Mapped[str | None] = mapped_column(Text)

    decision_doc_id: Mapped[int | None] = mapped_column(ForeignKey("processed_documents.id"))
    error: Mapped[str | None] = mapped_column(Text)


class SummaryRun(Base, TimestampMixin):
    __tablename__ = "summary_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cadence: Mapped[str | None] = mapped_column(String(20))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unsorted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    income_total_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expense_total_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(200))


class PdfPassword(Base, TimestampMixin):
    """Vault entry for password-protected PDFs. Tried in priority order
    (lower number wins), with sender/filename matchers favoured first."""

    __tablename__ = "pdf_passwords"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(160))
    password: Mapped[str] = mapped_column(String(500))         # encrypted at rest
    sender_match: Mapped[str | None] = mapped_column(String(200))
    filename_match: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)


class AppConfig(Base, TimestampMixin):
    """Runtime-editable settings written from the /settings page. Overlays
    the env-loaded `Settings` for the keys listed in
    `app.config.RUNTIME_OVERLAY_KEYS`."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


PERSONAL_SCOPE_NAME = "Personal"
