"""Auth API router — API_SPECIFICATION.md §1 endpoints, envelope-wrapped."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    MeEnvelope,
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
    """httpOnly refresh cookie (SECURITY §6: no tokens in localStorage).

    Flow:
        Set refresh_token, httponly, samesite=lax, 7 day max-age, path /api/v1/auth.

    Debug:
        Browser will not send the cookie to /api/v1/businesses — only auth routes.
        JS cannot read it; the SPA also receives refresh_token in the JSON body.
    """
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
    """POST /auth/otp/request. Wraps service.request_otp in {success, data}.

    Debug:
        Rate limit and identifier errors are AuthError → envelope, not this function.
    """
    data = await service.request_otp(session, body.identifier, body.purpose)
    return {"success": True, "data": data}


@router.post("/otp/verify")
async def otp_verify(
    body: OtpVerifyIn, response: Response, session: SessionDep
) -> dict[str, object]:
    """POST /auth/otp/verify. Sets refresh cookie and returns user + both tokens.

    Flow:
        service.verify_otp → _set_refresh_cookie → envelope with access and refresh.

    Debug:
        Cookie and JSON both carry the refresh token so non-browser clients can rotate.
    """
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
    """Rotate the refresh token. Body OR httpOnly cookie carries it.

    Flow:
        1. Prefer body.refresh_token, else cookie. Neither → TokenInvalid.
        2. service.refresh. On reuse, delete cookie then re-raise.
        3. Rewrite cookie; response data is access_token only.

    Debug:
        New refresh token is only in the cookie, not the JSON (unlike otp/verify).
    """
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
    """POST /auth/stepup. Bearer required. Returns {stepup_token}.

    Flow:
        service.stepup peeks identifiers, consumes the matching OTP, mints a step-up JWT.
    """
    data = await service.stepup(session, user_id, body.otp)
    return {"success": True, "data": data}


@router.post("/totp/setup")
async def totp_setup(session: SessionDep, user_id: UserDep) -> dict[str, object]:
    """POST /auth/totp/setup. Bearer required. Returns secret + qr_uri.

    Debug:
        409 TOTP_ALREADY_ENABLED if totp_enabled_at is already set.
    """
    return {"success": True, "data": await service.totp_setup(session, user_id)}


@router.post("/totp/verify")
async def totp_verify(
    body: TotpVerifyIn, session: SessionDep, user_id: UserDep
) -> dict[str, object]:
    """POST /auth/totp/verify. Bearer required. Sets totp_enabled_at on success.

    Debug:
        401 TOTP_INVALID if setup was skipped or the code is outside the ±30s window.
    """
    return {"success": True, "data": await service.totp_verify(session, user_id, body.code)}


@router.get("/me", response_model=MeEnvelope)
async def me(session: SessionDep, user_id: UserDep) -> dict[str, object]:
    """GET /auth/me. Bearer required. Returns user, business ids, primary firm id.

    Debug:
        firm null means the UI should use the client shell even if the user picked CA.
    """
    return {"success": True, "data": await service.me(session, user_id)}
