"""Minimal CA firm service for onboarding (API_SPECIFICATION.md §3)."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ServiceError
from app.core.auth import totp as totp_svc
from app.core.businesses.gstin import validate_pan
from app.db.models.core import CaFirm, CaFirmMember, FirmRole, User


async def create_firm(
    session: AsyncSession, user_id: uuid.UUID, firm_name: str, pan: str
) -> dict[str, object]:
    """POST /firm — create firm + PARTNER membership; requires TOTP enabled."""
    user = await session.get(User, user_id)
    if user is None:
        raise ServiceError("unknown user", 401, "INVALID_CREDENTIALS")
    if not totp_svc.is_enabled(user.totp_secret, user.totp_enabled_at):
        raise ServiceError("TOTP required before firm creation", 403, "TOTP_REQUIRED")
    validated_pan = validate_pan(pan)

    ca_code = "CAF-" + uuid.uuid4().hex[:12].upper()
    firm = CaFirm(firm_name=firm_name, pan=validated_pan, ca_code=ca_code)
    session.add(firm)
    await session.flush()
    member = CaFirmMember(
        firm_id=firm.id,
        user_id=user_id,
        role=FirmRole.PARTNER,
        can_export=True,
        can_revoke=True,
        can_invite_members=True,
    )
    session.add(member)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ServiceError("PAN or ca_code conflict", 409, "CONFLICT") from exc
    return {
        "id": str(firm.id),
        "firm_name": firm.firm_name,
        "pan": firm.pan,
        "ca_code": firm.ca_code,
    }
