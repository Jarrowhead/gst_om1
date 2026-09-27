"""Business + GST registration service layer (task 0.6)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ServiceError
from app.core.access import AccessDenied, audit
from app.core.businesses.gstin import gstin_state_code, validate_gstin, validate_pan
from app.db.models.core import Business, BusinessRole, BusinessUser, FilingScheme, GstRegistration


class RegistrationNotFound(AccessDenied):
    """404 when a registration is unknown or inaccessible."""

    code = "REGISTRATION_NOT_FOUND"


async def create_business(
    session: AsyncSession,
    *,
    legal_name: str,
    pan: str,
    trade_name: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Business:
    """Create a business (API_SPEC §2 POST /businesses)."""
    validated_pan = validate_pan(pan)
    existing = (
        await session.execute(select(Business).where(Business.pan == validated_pan))
    ).scalar_one_or_none()
    if existing is not None:
        raise ServiceError("PAN already registered", 409, "CONFLICT")
    business = Business(
        legal_name=legal_name,
        pan=validated_pan,
        trade_name=trade_name,
        created_by=created_by,
    )
    session.add(business)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ServiceError("PAN already registered", 409, "CONFLICT") from None
    if created_by is not None:
        session.add(
            BusinessUser(
                business_id=business.id,
                user_id=created_by,
                role=BusinessRole.OWNER,
            )
        )
    await audit(
        session,
        action="BUSINESS_CREATED",
        entity="business",
        entity_id=str(business.id),
        actor_user_id=created_by,
        business_id=business.id,
    )
    await session.commit()
    await session.refresh(business)
    return business


async def create_registration(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    gstin: str,
    registered_address: str | None = None,
    aato_minor: int | None = None,
    filing_scheme: FilingScheme = FilingScheme.REGULAR_MONTHLY,
) -> GstRegistration:
    """Attach a checksum-valid GSTIN to a business.

    API_SPEC §2 POST /businesses/{id}/registrations.
    """
    business = await session.get(Business, business_id)
    if business is None:
        raise AccessDenied("no access to this business")
    validated_gstin = validate_gstin(gstin, pan=business.pan)
    duplicate = (
        await session.execute(
            select(GstRegistration).where(GstRegistration.gstin == validated_gstin)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ServiceError("GSTIN already registered", 409, "CONFLICT")
    irn_applicable = (aato_minor or 0) > 5_000_000_000  # above 5 Cr threshold
    reg = GstRegistration(
        business_id=business_id,
        gstin=validated_gstin,
        state_code=gstin_state_code(validated_gstin),
        registered_address=registered_address,
        aato_latest_minor=aato_minor or 0,
        filing_scheme=filing_scheme,
        irn_applicable=irn_applicable,
    )
    session.add(reg)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ServiceError("GSTIN already registered", 409, "CONFLICT") from None
    await audit(
        session,
        action="REGISTRATION_CREATED",
        entity="gst_registration",
        entity_id=str(reg.id),
        business_id=business_id,
        registration_id=reg.id,
    )
    await session.commit()
    await session.refresh(reg)
    return reg


async def list_my_businesses(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """GET /businesses — businesses the user can access via client membership."""
    rows = (
        await session.execute(
            select(Business, BusinessUser.role)
            .join(BusinessUser, BusinessUser.business_id == Business.id)
            .where(BusinessUser.user_id == user_id)
            .order_by(Business.legal_name)
        )
    ).all()
    return [
        {
            "id": str(b.id),
            "legal_name": b.legal_name,
            "trade_name": b.trade_name,
            "pan": b.pan,
            "role": r.value if isinstance(r, BusinessRole) else str(r),
        }
        for b, r in rows
    ]


async def get_business_detail(
    session: AsyncSession,
    business_id: uuid.UUID,
) -> dict[str, Any]:
    """GET /businesses/{id} incl. registrations."""
    business = await session.get(Business, business_id)
    if business is None:
        raise AccessDenied("no access to this business")
    regs = (
        await session.execute(
            select(GstRegistration).where(GstRegistration.business_id == business_id)
        )
    ).scalars().all()
    return {
        "id": str(business.id),
        "legal_name": business.legal_name,
        "trade_name": business.trade_name,
        "pan": business.pan,
        "created_at": business.created_at.isoformat() if business.created_at else None,
        "registrations": [
            {
                "id": str(r.id),
                "gstin": r.gstin,
                "state_code": r.state_code,
                "filing_scheme": r.filing_scheme.value,
                "irn_applicable": r.irn_applicable,
                "aato_latest_minor": r.aato_latest_minor,
                "registered_address": r.registered_address,
            }
            for r in regs
        ],
    }


async def update_registration(
    session: AsyncSession,
    registration_id: uuid.UUID,
    *,
    registered_address: str | None = None,
    aato_minor: int | None = None,
    filing_scheme: FilingScheme | None = None,
) -> GstRegistration:
    """PATCH /registrations/{regId} — updates allowed fields only."""
    reg = await session.get(GstRegistration, registration_id)
    if reg is None:
        raise RegistrationNotFound("no access to this registration")
    dirty = False
    if registered_address is not None:
        reg.registered_address = registered_address
        dirty = True
    if aato_minor is not None:
        reg.aato_latest_minor = aato_minor
        reg.irn_applicable = aato_minor > 5_000_000_000
        dirty = True
    if filing_scheme is not None:
        reg.filing_scheme = filing_scheme
        dirty = True
    if dirty:
        await audit(
            session,
            action="REGISTRATION_UPDATED",
            entity="gst_registration",
            entity_id=str(reg.id),
            business_id=reg.business_id,
            registration_id=reg.id,
        )
        await session.commit()
        await session.refresh(reg)
    return reg


async def update_business(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    legal_name: str | None = None,
    trade_name: str | None = None,
) -> Business:
    """PATCH /businesses/{id} — updates allowed fields only."""
    business = await session.get(Business, business_id)
    if business is None:
        raise AccessDenied("no access to this business")
    dirty = False
    if legal_name is not None:
        business.legal_name = legal_name
        dirty = True
    if trade_name is not None:
        business.trade_name = trade_name
        dirty = True
    if dirty:
        await audit(
            session,
            action="BUSINESS_UPDATED",
            entity="business",
            entity_id=str(business.id),
            business_id=business.id,
        )
        await session.commit()
        await session.refresh(business)
    return business
