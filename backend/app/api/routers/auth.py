"""Auth API router — API_SPECIFICATION.md §1 endpoints, envelope-wrapped."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    OtpRequestIn,
    OtpVerifyIn,
    RefreshIn,
    StepUpIn,
    TotpVerifyIn,
)
from app.core.auth import service
from app.core.auth.dependencies import require_user
from app.core.auth.errors import RefreshReuseDetected, TokenInvalid
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]
RefreshCookieDep = Annotated[str | None, Cookie()]

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"
REFRESH_COOKIE_MAX_AGE = 7 * 24 * 3600


def _set_refresh_cookie(response: Response, token: str) -> None:
    """httpOnly refresh cookie (SECURITY §6: no tokens in localStorage)."""
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
    )


@router.post("/otp/request")
async def otp_request(body: OtpRequestIn, session: SessionDep) -> dict[str, object]:
    data = await service.request_otp(session, body.identifier, body.purpose)
    return {"success": True, "data": data}


@router.post("/otp/verify")
async def otp_verify(
    body: OtpVerifyIn, response: Response, session: SessionDep
) -> dict[str, object]:
    user, tokens = await service.verify_otp(session, body.identifier, body.otp)
    _set_refresh_cookie(response, tokens["refresh_token"])
    return {
        "success": True,
        "data": {
            "user": user,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
        },
    }


@router.post("/refresh")
async def refresh_tokens(
    body: RefreshIn,
    response: Response,
    session: SessionDep,
    refresh_token: RefreshCookieDep = None,
) -> dict[str, object]:
    """Rotate the refresh token. Body OR httpOnly cookie carries it."""
    token = body.refresh_token or refresh_token
    if not token:
        raise TokenInvalid("no refresh token presented")
    try:
        tokens, _user_id = await service.refresh(session, token)
    except RefreshReuseDetected:
        response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
        raise
    _set_refresh_cookie(response, tokens["refresh_token"])
    return {"success": True, "data": {"access_token": tokens["access_token"]}}


@router.post("/stepup")
async def stepup(body: StepUpIn, session: SessionDep, user_id: UserDep) -> dict[str, object]:
    data = await service.stepup(session, user_id, body.otp)
    return {"success": True, "data": data}


@router.post("/totp/setup")
async def totp_setup(session: SessionDep, user_id: UserDep) -> dict[str, object]:
    return {"success": True, "data": await service.totp_setup(session, user_id)}


@router.post("/totp/verify")
async def totp_verify(
    body: TotpVerifyIn, session: SessionDep, user_id: UserDep
) -> dict[str, object]:
    return {"success": True, "data": await service.totp_verify(session, user_id, body.code)}


@router.get("/me")
async def me(session: SessionDep, user_id: UserDep) -> dict[str, object]:
    return {"success": True, "data": {"user": await service.me(session, user_id)}}
