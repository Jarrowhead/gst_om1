"""extraction schema: documents, extraction jobs, invoice drafts.

Table shapes follow TECHNICAL_ARCHITECTURE.md §3 (`extraction` schema) verbatim.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db.base import Base
from app.db.models.core import CORE_SCHEMA
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

EXTRACTION_SCHEMA = "extraction"


class CaptureSource(enum.StrEnum):
    """`capture_source` routes preprocessing (scan vs photo/digital branch)."""

    PDF_SCAN = "PDF_SCAN"
    DIGITAL = "DIGITAL"
    PHOTO = "PHOTO"
    WHATSAPP = "WHATSAPP"


class JobStatus(enum.StrEnum):
    """Durable pipeline state; Redis holds job IDs only."""

    QUEUED = "QUEUED"
    PREPROCESS = "PREPROCESS"
    OCR_RUNNING = "OCR_RUNNING"
    LLM_RUNNING = "LLM_RUNNING"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CONFIRMED = "CONFIRMED"


class Document(Base):
    """Immutable; sha256 dedupe."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_reg_fp", "registration_id", "fp"),
        Index("ix_documents_sha256", "sha256"),
        {"schema": EXTRACTION_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)  # MMYYYY
    capture_source: Mapped[CaptureSource] = mapped_column(
        Enum(CaptureSource, name="capture_source", schema=EXTRACTION_SCHEMA),
        nullable=False,
    )
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    minio_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExtractionJob(Base):
    """Durable truth; Redis holds job IDs only."""

    __tablename__ = "extraction_jobs"
    __table_args__ = (
        Index("ix_extraction_jobs_document", "document_id"),
        Index("ix_extraction_jobs_status", "status"),
        {"schema": EXTRACTION_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{EXTRACTION_SCHEMA}.documents.id"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", schema=EXTRACTION_SCHEMA),
        nullable=False,
        default=JobStatus.QUEUED,
    )
    preproc_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ocr_text_ref: Mapped[str | None] = mapped_column(String(512))
    raw_llm_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_tokens_in: Mapped[int | None] = mapped_column(Integer)
    llm_tokens_out: Mapped[int | None] = mapped_column(Integer)
    confidence_avg: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class InvoiceDraft(Base):
    """LLM output pre-confirmation; never mixes with ledger."""

    __tablename__ = "invoice_drafts"
    __table_args__ = (
        Index("ix_invoice_drafts_job", "extraction_job_id"),
        {"schema": EXTRACTION_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    extraction_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{EXTRACTION_SCHEMA}.extraction_jobs.id"),
        nullable=False,
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
