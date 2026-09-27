"""Business + GST registration API router (task 0.6).

Routes:
  POST   /businesses
  GET    /businesses
  GET    /businesses/{business_id}
  PATCH  /businesses/{business_id}
  POST   /businesses/{business_id}/registrations
  GET    /registrations/{registration_id}
  PATCH  /registrations/{registration_id}

Every registration-scoped and business-scoped route goes through the access
 guard dependency (API_SPECIFICATION.md non-negotiable #1).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas_business import (
    BusinessCreateIn,
    BusinessOut,
    BusinessPatchIn,
    RegistrationCreateIn,
    RegistrationOut,
    RegistrationPatchIn,
)
from app.core.access import (
    BusinessAccess,
    RegistrationAccess,
    require_business_access,
    require_registration_access,
)
from app.core.auth.dependencies import require_user
from app.core.businesses import service
from app.db.models.core import FilingScheme, GstRegistration
from app.db.session import get_session

router = APIRouter(prefix="/businesses", tags=["businesses"])
RegRouter = APIRouter(prefix="/registrations", tags=["registrations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]


@router.post("")
async def create_business(
    body: BusinessCreateIn,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, Any]:
    business = await service.create_business(
        session,
        legal_name=body.legal_name,
        pan=body.pan,
        trade_name=body.trade_name,
        created_by=user_id,
    )
    return {
        "success": True,
        "data": BusinessOut(
            id=str(business.id),
            legal_name=business.legal_name,
            trade_name=business.trade_name,
            pan=business.pan,
            created_at=business.created_at.isoformat() if business.created_at else None,
        ).model_dump(),
    }


@router.get("")
async def list_businesses(session: SessionDep, user_id: UserDep) -> dict[str, Any]:
    return {"success": True, "data": await service.list_my_businesses(session, user_id)}


@router.get("/{business_id}")
async def get_business(
    access: Annotated[BusinessAccess, Depends(require_business_access("business_id"))],
    session: SessionDep,
) -> dict[str, Any]:
    detail = await service.get_business_detail(session, access.business_id)
    return {"success": True, "data": detail}


@router.patch("/{business_id}")
async def patch_business(
    body: BusinessPatchIn,
    access: Annotated[BusinessAccess, Depends(require_business_access("business_id"))],
    session: SessionDep,
) -> dict[str, Any]:
    business = await service.update_business(
        session,
        access.business_id,
        legal_name=body.legal_name,
        trade_name=body.trade_name,
    )
    return {
        "success": True,
        "data": BusinessOut(
            id=str(business.id),
            legal_name=business.legal_name,
            trade_name=business.trade_name,
            pan=business.pan,
            created_at=business.created_at.isoformat() if business.created_at else None,
        ).model_dump(),
    }


@router.post("/{business_id}/registrations")
async def create_registration(
    body: RegistrationCreateIn,
    access: Annotated[BusinessAccess, Depends(require_business_access("business_id"))],
    session: SessionDep,
) -> dict[str, Any]:
    scheme = FilingScheme.REGULAR_MONTHLY
    if body.filing_scheme:
        scheme = FilingScheme(body.filing_scheme)
    reg = await service.create_registration(
        session,
        business_id=access.business_id,
        gstin=body.gstin,
        registered_address=body.registered_address,
        aato_minor=body.aato_minor,
        filing_scheme=scheme,
    )
    return {"success": True, "data": _reg_out(reg)}


@RegRouter.get("/{registration_id}")
async def get_registration(
    access: Annotated[RegistrationAccess, Depends(require_registration_access("registration_id"))],
    session: SessionDep,
) -> dict[str, Any]:
    reg = await session.get(GstRegistration, access.registration_id)
    if reg is None:
        raise service.RegistrationNotFound("no access to this registration")
    return {"success": True, "data": _reg_out(reg)}


@RegRouter.patch("/{registration_id}")
async def patch_registration(
    body: RegistrationPatchIn,
    access: Annotated[RegistrationAccess, Depends(require_registration_access("registration_id"))],
    session: SessionDep,
) -> dict[str, Any]:
    scheme = FilingScheme(body.filing_scheme) if body.filing_scheme else None
    reg = await service.update_registration(
        session,
        access.registration_id,
        registered_address=body.registered_address,
        aato_minor=body.aato_minor,
        filing_scheme=scheme,
    )
    return {"success": True, "data": _reg_out(reg)}


def _reg_out(reg: GstRegistration) -> dict[str, Any]:
    return RegistrationOut(
        id=str(reg.id),
        business_id=str(reg.business_id),
        gstin=reg.gstin,
        state_code=reg.state_code,
        filing_scheme=reg.filing_scheme.value,
        irn_applicable=reg.irn_applicable,
        aato_latest_minor=reg.aato_latest_minor,
        registered_address=reg.registered_address,
        created_at=reg.created_at.isoformat() if reg.created_at else None,
    ).model_dump()
