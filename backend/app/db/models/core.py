"""core schema: users, firms, businesses, registrations, links, consent, audit.

Table shapes follow TECHNICAL_ARCHITECTURE.md §3 (`core` schema) verbatim.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from app.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

CORE_SCHEMA = "core"


class FirmRole(enum.StrEnum):
    """Role of a user inside a CA firm."""

    PARTNER = "PARTNER"
    CA = "CA"
    CLERK = "CLERK"


class BusinessRole(enum.StrEnum):
    """Role of a user on the client side of a business."""

    OWNER = "OWNER"
    CLERK = "CLERK"


class LinkStatus(enum.StrEnum):
    """Lifecycle of a CA<->client link."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class LinkInitiatedBy(enum.StrEnum):
    """Which side started the link."""

    FIRM_REQUEST = "FIRM_REQUEST"
    CLIENT_INVITE = "CLIENT_INVITE"


class FilingScheme(enum.StrEnum):
    """Filing scheme of a GST registration."""

    REGULAR_MONTHLY = "REGULAR_MONTHLY"
    QRMP = "QRMP"
    COMPOSITION = "COMPOSITION"


class User(Base):
    """A person — never a business. TOTP mandatory before joining a firm."""

    __tablename__ = "users"
    __table_args__ = {"schema": CORE_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    mobile: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mobile_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    firm_memberships: Mapped[list[CaFirmMember]] = relationship(
        back_populates="user"
    )
    business_memberships: Mapped[list[BusinessUser]] = relationship(
        back_populates="user"
    )


class CaFirm(Base):
    """CA firm — the client-facing + liability unit."""

    __tablename__ = "ca_firms"
    __table_args__ = {"schema": CORE_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    firm_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ca_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    pan: Mapped[str] = mapped_column(String(10), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    members: Mapped[list[CaFirmMember]] = relationship(back_populates="firm")
    client_links: Mapped[list[CaClientLink]] = relationship(back_populates="firm")


class CaFirmMember(Base):
    """Granular permissions; audit logs the *member*, never 'the firm'."""

    __tablename__ = "ca_firm_members"
    __table_args__ = (
        UniqueConstraint("firm_id", "user_id", name="uq_ca_firm_members_firm_user"),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    firm_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.ca_firms.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id"), nullable=False
    )
    role: Mapped[FirmRole] = mapped_column(
        Enum(FirmRole, name="firm_role", schema=CORE_SCHEMA), nullable=False
    )
    can_export: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_revoke: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_invite_members: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="firm_memberships")
    firm: Mapped[CaFirm] = relationship(back_populates="members")


class Business(Base):
    """PAN = legal entity key. NEVER invent PANs/GSTINs."""

    __tablename__ = "businesses"
    __table_args__ = {"schema": CORE_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    pan: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    registrations: Mapped[list[GstRegistration]] = relationship(
        back_populates="business"
    )
    users: Mapped[list[BusinessUser]] = relationship(back_populates="business")
    client_links: Mapped[list[CaClientLink]] = relationship(
        back_populates="business"
    )


class GstRegistration(Base):
    """Every filing object hangs off a registration."""

    __tablename__ = "gst_registrations"
    __table_args__ = (
        Index("ix_gst_registrations_business", "business_id"),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.businesses.id"), nullable=False
    )
    gstin: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    filing_scheme: Mapped[FilingScheme] = mapped_column(
        Enum(FilingScheme, name="filing_scheme", schema=CORE_SCHEMA),
        nullable=False,
        default=FilingScheme.REGULAR_MONTHLY,
    )
    irn_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    aato_latest_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    registered_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    business: Mapped[Business] = relationship(back_populates="registrations")


class BusinessUser(Base):
    """Client-side staff only."""

    __tablename__ = "business_users"
    __table_args__ = (
        UniqueConstraint("business_id", "user_id", name="uq_business_users_biz_user"),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.businesses.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id"), nullable=False
    )
    role: Mapped[BusinessRole] = mapped_column(
        Enum(BusinessRole, name="business_role", schema=CORE_SCHEMA), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="business_memberships")
    business: Mapped[Business] = relationship(back_populates="users")


class CaClientLink(Base):
    """One active row per (firm, business); covers ALL registrations."""

    __tablename__ = "ca_client_links"
    __table_args__ = (
        Index(
            "ix_ca_client_links_firm_business_status",
            "ca_firm_id",
            "business_id",
            "status",
        ),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    ca_firm_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.ca_firms.id"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.businesses.id"), nullable=False
    )
    status: Mapped[LinkStatus] = mapped_column(
        Enum(LinkStatus, name="link_status", schema=CORE_SCHEMA), nullable=False
    )
    initiated_by: Mapped[LinkInitiatedBy] = mapped_column(
        Enum(
            LinkInitiatedBy, name="link_initiated_by", schema=CORE_SCHEMA
        ),
        nullable=False,
    )
    invite_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    consent_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.consent_records.id")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    firm: Mapped[CaFirm] = relationship(back_populates="client_links")
    business: Mapped[Business] = relationship(back_populates="client_links")


class ConsentRecord(Base):
    """DPDP: versioned, withdrawable."""

    __tablename__ = "consent_records"
    __table_args__ = {"schema": CORE_SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    principal_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id"), nullable=False
    )
    fiduciary_type: Mapped[str] = mapped_column(String(30), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    consent_text_version: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Every CA view/export logged."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity", "entity_id"),
        Index("ix_audit_logs_at", "at"),
        {"schema": CORE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.users.id")
    )
    ca_firm_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.ca_firms.id")
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.businesses.id")
    )
    registration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey(f"{CORE_SCHEMA}.gst_registrations.id")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    payload_diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
