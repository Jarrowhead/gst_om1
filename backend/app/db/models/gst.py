"""gst schema: series, invoices, CDNs, periods, exports, amendments, e-invoices,
3B, 2B, ITC recon, notifications.

Table shapes follow TECHNICAL_ARCHITECTURE.md §3 (`gst` schema) verbatim.
Money columns are integer minor units (paise) — no float ever.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.base import Base
from app.db.models.core import CORE_SCHEMA, GstRegistration
from app.db.models.extraction import EXTRACTION_SCHEMA, Document
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

GST_SCHEMA = "gst"


class InvoiceDirection(enum.StrEnum):
    """Sales (outward, GSTR-1) vs purchase (inward, for ITC/2B recon)."""

    SALES = "SALES"
    PURCHASE = "PURCHASE"


class SupplyType(enum.StrEnum):
    """Intra-state (CGST+SGST) vs inter-state (IGST)."""

    INTRA = "INTRA"
    INTER = "INTER"


class InvoiceStatus(enum.StrEnum):
    """DRAFT -> CONFIRMED -> LOCKED (once the period is filed)."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    LOCKED = "LOCKED"


class SeriesDocType(enum.StrEnum):
    """Document kinds with series."""

    INV = "INV"
    CDN = "CDN"
    DBN = "DBN"


class InvType(enum.StrEnum):
    """GSTR-1 invoice type discriminator."""

    R = "R"
    SEWP = "SEWP"
    SEWOP = "SEWOP"
    DE = "DE"


class ExportPayType(enum.StrEnum):
    """WPAY = export with payment; WOPAY = without payment."""

    WPAY = "WPAY"
    WOPAY = "WOPAY"


class FilingStatus(enum.StrEnum):
    """Period lifecycle: OPEN -> READY_FOR_FILING -> FILED."""

    OPEN = "OPEN"
    READY_FOR_FILING = "READY_FOR_FILING"
    FILED = "FILED"


class NoteType(enum.StrEnum):
    """First-class note entities -> CDNR / CDNUR."""

    CDN = "CDN"
    DBN = "DBN"


class NoteStatus(enum.StrEnum):
    """Note lifecycle mirrors invoices."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    LOCKED = "LOCKED"


class ExportType(enum.StrEnum):
    """Original export vs amendment export (GSTR-1A)."""

    ORIGINAL = "ORIGINAL"
    AMENDMENT = "AMENDMENT"


class AmendmentStatus(enum.StrEnum):
    """1A delta lifecycle."""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    EXPORTED = "EXPORTED"


class Gstr2bSource(enum.StrEnum):
    """How a 2B statement arrived."""

    PORTAL_UPLOAD = "PORTAL_UPLOAD"
    GSP_API = "GSP_API"


class MatchStatus(enum.StrEnum):
    """5-status reconciliation verdict."""

    MATCHED = "MATCHED"
    PROBABLE = "PROBABLE"
    UNMATCHED = "UNMATCHED"
    MISSING_IN_2B = "MISSING_IN_2B"
    MISSING_IN_BOOKS = "MISSING_IN_BOOKS"


class DocumentSeries(Base):
    """Feeds doc_issue ranges."""

    __tablename__ = "document_series"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "doc_type", "series_code", "fy",
            name="uq_document_series_reg_type_code_fy",
        ),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    doc_type: Mapped[SeriesDocType] = mapped_column(
        Enum(SeriesDocType, name="series_doc_type", schema=GST_SCHEMA), nullable=False
    )
    series_code: Mapped[str] = mapped_column(String(20), nullable=False)
    fy: Mapped[str] = mapped_column(String(7), nullable=False)  # e.g. 2026-27
    current_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Invoice(Base):
    """Confirmed invoice ledger row; LOCKED once period filed; corrections via 1A."""

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_reg_fp", "registration_id", "fp"),
        UniqueConstraint(
            "registration_id", "fp", "invoice_no",
            name="uq_invoices_reg_fp_no",
        ),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)  # MMYYYY
    direction: Mapped[InvoiceDirection] = mapped_column(
        Enum(InvoiceDirection, name="invoice_direction", schema=GST_SCHEMA),
        nullable=False,
    )
    supplier_gstin: Mapped[str | None] = mapped_column(String(15))
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    invoice_no: Mapped[str] = mapped_column(String(32), nullable=False)
    series: Mapped[str | None] = mapped_column(String(20))
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    place_of_supply: Mapped[str] = mapped_column(String(2), nullable=False)
    supply_type: Mapped[SupplyType] = mapped_column(
        Enum(SupplyType, name="supply_type", schema=GST_SCHEMA), nullable=False
    )
    rchrg: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inv_typ: Mapped[InvType] = mapped_column(
        Enum(InvType, name="inv_typ", schema=GST_SCHEMA),
        nullable=False,
        default=InvType.R,
    )
    export_pay_type: Mapped[ExportPayType | None] = mapped_column(
        Enum(ExportPayType, name="export_pay_type", schema=GST_SCHEMA)
    )
    shipping_bill_no: Mapped[str | None] = mapped_column(String(32))
    port_code: Mapped[str | None] = mapped_column(String(10))
    total_value_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{EXTRACTION_SCHEMA}.documents.id")
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status", schema=GST_SCHEMA),
        nullable=False,
        default=InvoiceStatus.DRAFT,
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    e_invoice: Mapped[EInvoice | None] = relationship(
        back_populates="invoice", uselist=False
    )


class InvoiceLine(Base):
    """Tax always recomputed server-side — stored values are an audit trail."""

    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_no", name="uq_invoice_lines_inv_line"),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.invoices.id"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    uqc: Mapped[str | None] = mapped_column(String(3))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    unit_price_minor: Mapped[int | None] = mapped_column(Integer)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    taxable_value_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sgst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    igst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cess_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class CreditDebitNote(Base):
    """First-class entity -> CDNR/CDNUR."""

    __tablename__ = "credit_debit_notes"
    __table_args__ = (
        Index("ix_credit_debit_notes_reg_fp", "registration_id", "fp"),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)
    note_type: Mapped[NoteType] = mapped_column(
        Enum(NoteType, name="note_type", schema=GST_SCHEMA), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(16), nullable=False)
    source_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.invoices.id")
    )
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    party_name: Mapped[str | None] = mapped_column(String(255))
    taxable_value_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sgst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    igst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cess_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    series: Mapped[str | None] = mapped_column(String(20))
    note_no: Mapped[str] = mapped_column(String(32), nullable=False)
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[NoteStatus] = mapped_column(
        Enum(NoteStatus, name="note_status", schema=GST_SCHEMA),
        nullable=False,
        default=NoteStatus.DRAFT,
    )
    irn: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FilingPeriod(Base):
    """Deadline engine drives reminders; composite PK (registration, fp)."""

    __tablename__ = "filing_periods"
    __table_args__ = {"schema": GST_SCHEMA}

    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"),
        primary_key=True,
    )
    fp: Mapped[str] = mapped_column(String(6), primary_key=True)  # MMYYYY
    scheme_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[FilingStatus] = mapped_column(
        Enum(FilingStatus, name="filing_status", schema=GST_SCHEMA),
        nullable=False,
        default=FilingStatus.OPEN,
    )
    gstr1_due_date: Mapped[date | None] = mapped_column(Date)
    gstr3b_due_date: Mapped[date | None] = mapped_column(Date)
    iff_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nil_return: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )

    registration: Mapped[GstRegistration] = relationship()


class Gstr1Export(Base):
    """Versioned + immutable."""

    __tablename__ = "gstr1_exports"
    __table_args__ = (
        Index("ix_gstr1_exports_reg_fp", "registration_id", "fp"),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )
    json_minio_key: Mapped[str] = mapped_column(String(512), nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, nullable=False)
    totals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    export_type: Mapped[ExportType] = mapped_column(
        Enum(ExportType, name="gstr1_export_type", schema=GST_SCHEMA),
        nullable=False,
        default=ExportType.ORIGINAL,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Gstr1aAmendment(Base):
    """Delta records; originals never edited."""

    __tablename__ = "gstr1a_amendments"
    __table_args__ = {"schema": GST_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    target_export_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.gstr1_exports.id"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.invoices.id")
    )
    cdn_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.credit_debit_notes.id")
    )
    field_deltas: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AmendmentStatus] = mapped_column(
        Enum(AmendmentStatus, name="amendment_status", schema=GST_SCHEMA),
        nullable=False,
        default=AmendmentStatus.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EInvoice(Base):
    """IRN path storage."""

    __tablename__ = "e_invoices"
    __table_args__ = {"schema": GST_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.invoices.id"), nullable=False, unique=True
    )
    irn: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ack_no: Mapped[str | None] = mapped_column(String(32))
    ack_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_qr_base64: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_window_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    invoice: Mapped[Invoice] = relationship(back_populates="e_invoice")


class Gstr3bExport(Base):
    """Outward locked per Jul-2025; overrides only ITC tables."""

    __tablename__ = "gstr3b_exports"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "fp", name="uq_gstr3b_exports_reg_fp"
        ),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)
    auto_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manual_overrides: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Gstr2bStatement(Base):
    """Portal 2B JSON until GSP fetch."""

    __tablename__ = "gstr2b_statements"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "fp", name="uq_gstr2b_statements_reg_fp"
        ),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)
    source: Mapped[Gstr2bSource] = mapped_column(
        Enum(Gstr2bSource, name="gstr2b_source", schema=GST_SCHEMA), nullable=False
    )
    raw_minio_key: Mapped[str] = mapped_column(String(512), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    imported_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )


class Gstr2bEntry(Base):
    """Parsed 2B rows."""

    __tablename__ = "gstr2b_entries"
    __table_args__ = (
        Index("ix_gstr2b_entries_statement", "statement_id"),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.gstr2b_statements.id"), nullable=False
    )
    supplier_gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    taxable_value_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sgst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    igst_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cess_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    itc_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)


class ItcReconciliation(Base):
    """The report CAs pay for."""

    __tablename__ = "itc_reconciliation"
    __table_args__ = (
        Index("ix_itc_reconciliation_reg_fp", "registration_id", "fp"),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id"), nullable=False
    )
    fp: Mapped[str] = mapped_column(String(6), nullable=False)
    purchase_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.invoices.id")
    )
    gstr2b_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{GST_SCHEMA}.gstr2b_entries.id")
    )
    match_status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, name="match_status", schema=GST_SCHEMA), nullable=False
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    remarks: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    """In-app store."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        {"schema": GST_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id"), nullable=False
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.businesses.id")
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Imported last so the name never shadows the class defined above; typing-only
# use in annotations above is quoted so mypy resolves it lazily.
_ = (Document,)  # documents relationship target (unused FK backref in v2 shape)
