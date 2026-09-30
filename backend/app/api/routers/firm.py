"""CA firm API router — onboarding-only (API_SPECIFICATION.md §3)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FirmCreateIn, FirmEnvelope
from app.core.auth.dependencies import require_user
from app.core.firms import service as firm_service
from app.db.session import get_session

router = APIRouter(prefix="/firm", tags=["firm"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]


@router.post("", response_model=FirmEnvelope)
async def create_firm(
    body: FirmCreateIn, session: SessionDep, user_id: UserDep
) -> dict[str, object]:
    """POST /firm. Bearer required. Delegates to firms.service.create_firm.

    Flow:
        TOTP check and PAN validation happen in the service, not here.

    Debug:
        403 TOTP_REQUIRED until /auth/totp/verify has set totp_enabled_at.
    """
    data = await firm_service.create_firm(
        session, user_id, body.firm_name, body.pan
    )
    return {"success": True, "data": data}
