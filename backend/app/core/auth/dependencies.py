"""JWT bearer + step-up dependencies — the auth slice of the task 0.5 guard.

Only authentication (who are you). Authorization (which business/registration)
lands in task 0.5's require_business_access on top of this.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.errors import (
    InvalidCredentials,
    StepUpRequired,
    TokenInvalid,
)
from app.core.auth.otp import verify_otp
from app.core.auth.redis_client import get_redis
from app.core.auth.tokens import verify_access_token, verify_stepup_token
from app.db.models.core import User
from app.db.session import get_session

bearer_scheme = HTTPBearer(auto_error=False)


async def require_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> uuid.UUID:
    """Strict auth: a valid Bearer access token is required.

    Flow:
        Missing credentials → TokenInvalid. Else verify_access_token → user UUID.

    Debug:
        401 missing bearer token vs invalid access token distinguishes header absence from bad JWT.
    """
    if credentials is None:
        raise TokenInvalid("missing bearer token")
    return verify_access_token(credentials.credentials)


async def optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> uuid.UUID | None:
    """Lenient auth: None when unauthenticated (public endpoints).

    Flow:
        No header → None. Bad token → None (swallows TokenInvalid). Valid → user UUID.

    Debug:
        Do not use this on tenant routes; a bad token looks the same as logged-out.
    """
    if credentials is None:
        return None
    try:
        return verify_access_token(credentials.credentials)
    except TokenInvalid:
        return None


async def require_stepup(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(require_user)],
) -> uuid.UUID:
    """Step-up proof for sensitive routes (SECURITY §1 step-up list).

    Flow:
        1. X-Stepup-Token: verify JWT and require sub == bearer user_id.
        2. Else X-OTP: load user, verify_otp against email or mobile (consumes OTP).
        3. Neither header → StepUpRequired.

    Debug:
        Token for a different user → 'does not match the bearer identity'. OTP path uses
        email or mobile only, not the dual-peek logic in service.stepup.
    """
    stepup_header = request.headers.get("X-Stepup-Token")
    if stepup_header:
        verified = verify_stepup_token(stepup_header)
        if verified != user_id:
            raise TokenInvalid("step-up token does not match the bearer identity")
        return user_id

    otp_header = request.headers.get("X-OTP")
    if not otp_header:
        raise StepUpRequired("X-OTP or X-Stepup-Token required on this route")

    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentials("unknown user")
    identifier = user.email or user.mobile
    redis: Redis = get_redis()
    await verify_otp(redis, identifier, otp_header)
    return user_id
