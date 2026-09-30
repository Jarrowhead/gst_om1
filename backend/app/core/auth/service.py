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
    OtpExpired,
    OtpInvalid,
    TokenInvalid,
    TotpInvalid,
)
from app.core.auth.redis_client import get_redis
from app.db.models.core import BusinessUser, CaFirm, CaFirmMember, User


def _user_out(user: User) -> dict[str, object]:
    """Serialize a User row for API envelopes (no password or TOTP secret).

    Flow:
        id, mobile, email, full_name, totp_enabled = totp_enabled_at is not None.

    Debug:
        totp_enabled false after verify → totp_enabled_at not committed in totp_verify.
    """
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
    """POST /auth/otp/request — rate-limited OTP issuance (dev echoes dev_otp).

    Flow:
        1. get_redis().
        2. otp_svc.request_otp (validate, rate limit, store).
        3. Return {otp_sent, dev_otp}. Session is unused (OTP lives in Redis).

    Debug:
        429 before any SMS — Redis counter otp_rl. session argument is for router symmetry.
    """
    redis = get_redis()
    result = await otp_svc.request_otp(redis, identifier, purpose)
    return {"otp_sent": result.otp_sent, "dev_otp": result.dev_otp}


async def verify_otp(
    session: AsyncSession, identifier: str, code: str
) -> tuple[dict[str, object], dict[str, str]]:
    """POST /auth/otp/verify — consume OTP and issue access + refresh tokens.

    Flow:
        1. otp_svc.verify_otp returns purpose (REGISTER or LOGIN).
        2. Look up User by mobile OR email.
        3. Missing user + REGISTER → insert User (email users get placeholder mobile).
        4. Missing user + LOGIN → InvalidCredentials.
        5. create_access_token + issue_refresh_family; return user dict and token pair.

    Debug:
        'register first' on a new mobile means purpose was not REGISTER. Placeholder mobile
        starts with 0 so it cannot collide with real Indian mobiles.
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

    Flow:
        '0' + 13 hex chars from uuid4.

    Debug:
        A reserved 0-prefix never collides with real Indian mobiles (10 digits, leading 6-9).
    """
    return "0" + uuid.uuid4().hex[:13]


async def refresh(
    session: AsyncSession, refresh_token: str
) -> tuple[dict[str, str], str]:
    """POST /auth/refresh — rotate; reuse of a rotated token kills the family.

    Flow:
        1. rotate_refresh_token (tombstone check, mint sibling).
        2. New access JWT for that user_id.
        3. Return token dict and user_id string. Cookie rewrite is the router's job.

    Debug:
        RefreshReuseDetected bubbles to the router, which deletes the cookie.
    """
    redis = get_redis()
    new_refresh, user_id = await token_svc.rotate_refresh_token(redis, refresh_token)
    access = token_svc.create_access_token(user_id)
    return {"access_token": access, "refresh_token": new_refresh}, str(user_id)


async def stepup(session: AsyncSession, user_id: uuid.UUID, otp: str) -> dict[str, str]:
    """POST /auth/stepup — fresh OTP proves the user for sensitive routes.

    Flow:
        1. Load user; collect email and mobile.
        2. peek_otp each identifier (no attempt burn).
        3. No live OTP → OtpExpired. No code match → OtpInvalid without touching counters.
        4. verify_otp on the matching identifier (deletes it), then create_stepup_token.
        Email is checked before mobile when both codes match (idents order).

    Debug:
        Wrong OTP here does not increment attempts (peek-only miss). Exhausted attempts
        only happen inside verify_otp after a match is chosen.
    """
    redis = get_redis()
    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentials("unknown user")
    idents = [i for i in (user.email, user.mobile) if i]
    if not idents:
        raise InvalidCredentials("no identifier available for step-up")
    live = [(i, r) for i in idents if (r := await otp_svc.peek_otp(redis, i)) is not None]
    if not live:
        raise OtpExpired("no active OTP for this identifier")
    match = next((i for i, r in live if r["code"] == otp), None)  # identical codes -> email first
    if match is None:
        raise OtpInvalid("incorrect OTP")  # no counter touched anywhere
    await otp_svc.verify_otp(redis, match, otp)  # attempts++, delete, raises on exhausted
    return {"stepup_token": token_svc.create_stepup_token(user_id)}


async def _identifier_of(session: AsyncSession, user_id: uuid.UUID) -> str:
    """Prefer email, else mobile, for OTP/TOTP account labels.

    Flow:
        session.get User; missing → InvalidCredentials; return email or mobile.

    Debug:
        Email-first accounts have a placeholder mobile; this still returns email first.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise InvalidCredentials("unknown user")
    return user.email or user.mobile


async def totp_setup(session: AsyncSession, user_id: uuid.UUID) -> dict[str, str]:
    """POST /auth/totp/setup — mint + store a pending secret, return QR URI.

    Flow:
        1. Load user; assert_not_enabled (409 if already on).
        2. generate_secret, save totp_secret, commit (enabled_at stays null).
        3. Return secret + otpauth qr_uri.

    Debug:
        Secret saved before the user scans. A failed verify leaves a pending secret
        that setup can replace until enabled_at is set.
    """
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
    """POST /auth/totp/verify — a valid code sets totp_enabled_at.

    Flow:
        1. No totp_secret → TotpInvalid (setup not called).
        2. verify_code fails → TotpInvalid (secret kept).
        3. Set totp_enabled_at now(UTC), commit, return {enabled: True}.

    Debug:
        Firm create still 403 → enabled_at null or is_enabled sees a different user row.
    """
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
    """Firm-join precondition (SECURITY §1); raises TotpInvalid if not verified.

    Flow:
        Load user; missing user or null totp_enabled_at → TotpInvalid.

    Debug:
        create_firm uses is_enabled (403 ServiceError), not this helper.
    """
    user = await session.get(User, user_id)
    if user is None or user.totp_enabled_at is None:
        raise TotpInvalid("TOTP setup + verification required before firm membership")


async def update_user_totp_secret(
    session: AsyncSession, user_id: uuid.UUID, secret: str
) -> None:
    """Reset path (step-up protected at the route layer).

    Flow:
        UPDATE users SET totp_secret WHERE id; commit. Does not clear totp_enabled_at.

    Debug:
        After reset the old authenticator codes fail until the client re-verifies.
    """
    await session.execute(
        update(User).where(User.id == user_id).values(totp_secret=secret)
    )
    await session.commit()


async def me(session: AsyncSession, user_id: uuid.UUID) -> dict[str, object]:
    """GET /auth/me — role-resolved profile (business ids + earliest firm).

    Flow:
        1. Load user or TokenInvalid.
        2. business_users → list of business ids.
        3. firm memberships ordered by firm.created_at, joined_at; first firm id or null.

    Debug:
        CA with no firm yet returns firm=null so the UI treats them as a client shell.
        Multiple firms: only the earliest is returned (primary).
    """
    user = await session.get(User, user_id)
    if user is None:
        raise TokenInvalid("user no longer exists")
    business_rows = await session.execute(
        select(BusinessUser.business_id).where(BusinessUser.user_id == user_id)
    )
    business_ids = [str(row[0]) for row in business_rows]
    firm_rows = (
        await session.execute(
            select(CaFirmMember)
            .join(CaFirm, CaFirm.id == CaFirmMember.firm_id)
            .where(CaFirmMember.user_id == user_id)
            .order_by(CaFirm.created_at.asc(), CaFirmMember.joined_at.asc())
        )
    ).scalars().all()
    firm_id = str(firm_rows[0].firm_id) if firm_rows else None
    return {
        "user": _user_out(user),
        "businesses": business_ids,
        "firm": firm_id,
    }
