"""Auth service — orchestrates OTP/JWT/TOTP with the users table."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import otp as otp_svc
from app.core.auth import tokens as token_svc
from app.core.auth import totp as totp_svc
from app.core.auth.errors import (
    InvalidCredentials,
    TokenInvalid,
    TotpInvalid,
)
from app.core.auth.redis_client import get_redis
from app.db.models.core import User


def _user_out(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "mobile": user.mobile,
        "email": user.email,
        "full_name": user.full_name,
        "totp_enabled": user.totp_enabled_at is not None,
    }


async def request_otp(
    session: AsyncSession, identifier: str, purpose: str
) -> dict[str, object]:
    """POST /auth/otp/request — rate-limited OTP issuance (dev echoes dev_otp)."""
    redis = get_redis()
    result = await otp_svc.request_otp(redis, identifier, purpose)
    return {"otp_sent": result.otp_sent, "dev_otp": result.dev_otp}


async def verify_otp(
    session: AsyncSession, identifier: str, code: str
) -> tuple[dict[str, object], dict[str, str]]:
    """POST /auth/otp/verify — LOGIN only (auto-register is Phase-1 onboarding).

    New users must exist before login; a LOGIN verify for an unknown user
    raises InvalidCredentials. Registration creates users in task 0.7's
    onboarding flow via purpose=REGISTER.
    """
    redis = get_redis()
    purpose = await otp_svc.verify_otp(redis, identifier, code)
    user = (
        await session.execute(
            select(User).where(
                (User.mobile == identifier) | (User.email == identifier)
            )
        )
    ).scalar_one_or_none()
    if user is None:
        if purpose == "REGISTER":
            full_name = f"User {identifier[-4:]}"
            user = User(
                mobile=identifier if "@" not in identifier else _placeholder_mobile(),
                email=identifier if "@" in identifier else None,
                full_name=full_name,
                mobile_verified_at=dt.datetime.now(tz=dt.UTC),
            )
            session.add(user)
            await session.commit()
        else:
            raise InvalidCredentials("no account for this identifier — register first")

    access = token_svc.create_access_token(user.id)
    refresh = await token_svc.issue_refresh_family(redis, user.id)
    return _user_out(user), {"access_token": access, "refresh_token": refresh}


def _placeholder_mobile() -> str:
    """Email-first users need a unique non-null mobile (users.mobile NOT NULL).

    A reserved 0-prefix never collides with real Indian mobiles (10 digits,
    leading 6-9).
    """
    return "0" + uuid.uuid4().hex[:13]


async def refresh(
    session: AsyncSession, refresh_token: str
) -> tuple[dict[str, str], str]:
    """POST /auth/refresh — rotate; reuse of a rotated token kills the family.

    Returns ((access_token, refresh_token), user_id) — the access JWT is fresh;
    the new refresh token MUST reach the client (httpOnly cookie at wiring).
    """
    redis = get_redis()
    new_refresh, user_id = await token_svc.rotate_refresh_token(redis, refresh_token)
    access = token_svc.create_access_token(user_id)
    return {"access_token": access, "refresh_token": new_refresh}, str(user_id)


async def stepup(session: AsyncSession, user_id: uuid.UUID, otp: str) -> dict[str, str]:
    """POST /auth/stepup — fresh OTP proves the user for sensitive routes."""
    redis = get_redis()
    await otp_svc.verify_otp(redis, await _identifier_of(session, user_id), otp)
    return {"stepup_token": token_svc.create_stepup_token(user_id)}


async def _identifier_of(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentials("unknown user")
    return user.email or user.mobile


async def totp_setup(session: AsyncSession, user_id: uuid.UUID) -> dict[str, str]:
    """POST /auth/totp/setup — mint + store a pending secret, return QR URI."""
    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentials("unknown user")
    totp_svc.assert_not_enabled(user.totp_secret, user.totp_enabled_at)
    secret = totp_svc.generate_secret()
    user.totp_secret = secret
    await session.commit()
    identifier = user.email or user.mobile
    return {
        "secret": secret,
        "qr_uri": totp_svc.provisioning_uri(secret, identifier),
    }


async def totp_verify(session: AsyncSession, user_id: uuid.UUID, code: str) -> dict[str, bool]:
    """POST /auth/totp/verify — client-side verify flips totp_enabled_at."""
    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentials("unknown user")
    if user.totp_secret is None:
        raise TotpInvalid("no TOTP setup in progress — call /auth/totp/setup first")
    if not totp_svc.verify_code(user.totp_secret, code):
        raise TotpInvalid("invalid TOTP code")
    user.totp_enabled_at = dt.datetime.now(tz=dt.UTC)
    await session.commit()
    return {"enabled": True}


async def require_totp_enabled(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Firm-join precondition (SECURITY §1); task 0.5's guard calls this."""
    user = await session.get(User, user_id)
    if user is None or user.totp_enabled_at is None:
        raise TotpInvalid("TOTP setup + verification required before firm membership")


async def update_user_totp_secret(
    session: AsyncSession, user_id: uuid.UUID, secret: str
) -> None:
    """Reset path (step-up protected at the route layer)."""
    await session.execute(
        update(User).where(User.id == user_id).values(totp_secret=secret)
    )
    await session.commit()


async def me(session: AsyncSession, user_id: uuid.UUID) -> dict[str, object]:
    """GET /auth/me — role-resolved profile (business/firm ids for now)."""
    user = await session.get(User, user_id)
    if user is None:
        raise TokenInvalid("user no longer exists")
    return _user_out(user)
