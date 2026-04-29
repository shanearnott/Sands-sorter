from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class Property(Base, TimestampMixin):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    drive_folder_id: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    rules: Mapped[list["Rule"]] = relationship(back_populates="property")


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rules: Mapped[list["Rule"]] = relationship(back_populates="category")


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    default_property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"))
    default_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    notes: Mapped[str | None] = mapped_column(Text)


class Rule(Base, TimestampMixin):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_type: Mapped[MatchType] = mapped_column(Enum(MatchType, name="match_type"))
    pattern: Mapped[str] = mapped_column(String(500))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[RuleSource] = mapped_column(
        Enum(RuleSource, name="rule_source"), default=RuleSource.manual, nullable=False
    )

    property: Mapped[Property] = relationship(back_populates="rules")
    category: Mapped[Category] = relationship(back_populates="rules")


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind, name="source_kind"))
    label: Mapped[str] = mapped_column(String(120))
    oauth_secret_name: Mapped[str | None] = mapped_column(String(200))
    cursor: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    year: Mapped[int | None] = mapped_column(Integer)
    drive_file_id: Mapped[str | None] = mapped_column(String(120))
    drive_path: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[DocStatus] = mapped_column(Enum(DocStatus, name="doc_status"))
    error: Mapped[str | None] = mapped_column(Text)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reassignment(Base, TimestampMixin):
    __tablename__ = "reassignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("processed_documents.id"))
    old_property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"))
    old_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    new_property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    new_category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    created_rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"))


class DailySummary(Base, TimestampMixin):
    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unsorted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(200))
