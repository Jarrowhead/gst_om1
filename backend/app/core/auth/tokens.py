"""JWT access tokens + rotating refresh token families (SECURITY §1).

Design:
  access  — short-lived (15 min) JWT, carries user_id; stateless verification.
  refresh — opaque random token; the *server* stores family state in Redis:
    rfam:{family_id} -> {member -> user_id}  TTL 7d
    rtk:{token}      -> family_id            TTL 7d
  Rotation: on /auth/refresh the presented token's family mints a NEW token and
  RETIRES the old one (moved to rtold:{token} keeping a tombstone). Reuse of a
  retired/unknown-but-family-shaped token kills the whole family (refresh
  rotation reuse detection) and raises RefreshReuseDetected.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Any, TypedDict

import jwt
from redis.asyncio import Redis

from app.config import get_settings
from app.core.auth.errors import (
    RefreshReuseDetected,
    TokenInvalid,
)

REFRESH_TTL_SECONDS = 7 * 24 * 3600


class TokenPair(TypedDict):
    access_token: str
    refresh_token: str


def _now() -> dt.datetime:
    """UTC now used for JWT iat/exp so tests can reason about relative expiry."""
    return dt.datetime.now(tz=dt.UTC)


def create_access_token(user_id: uuid.UUID) -> str:
    """15-min JWT access token (SECURITY §1).

    Flow:
        1. Payload sub=user_id, typ=access, iat/exp from settings.access_token_minutes.
        2. HS256 encode with jwt_secret.

    Debug:
        401 TOKEN_INVALID on every call → GST_JWT_SECRET mismatch between issuer and verifier.
    """
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "typ": "access",
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_access_token(token: str) -> uuid.UUID:
    """Validate signature/expiry/typ; return the user id.

    Flow:
        1. jwt.decode; PyJWTError → TokenInvalid.
        2. typ must be 'access' (step-up tokens fail here).
        3. Parse sub as UUID.

    Debug:
        'wrong token type' means a stepup JWT was sent as Bearer.
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalid("invalid access token") from exc
    if payload.get("typ") != "access":
        raise TokenInvalid("wrong token type")
    try:
        return uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenInvalid("malformed subject claim") from exc


def create_stepup_token(user_id: uuid.UUID) -> str:
    """15-min step-up proof for sensitive routes (SECURITY §1 step-up list).

    Flow:
        Same as access JWT but typ=stepup and stepup_token_minutes.

    Debug:
        Header X-Stepup-Token must be this string, not the access token.
    """
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "typ": "stepup",
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.stepup_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_stepup_token(token: str) -> uuid.UUID:
    """Validate a step-up token; returns the user id.

    Flow:
        1. Decode JWT.
        2. Reject typ != stepup.
        3. Return UUID sub.

    Debug:
        require_stepup also checks this UUID equals the bearer user.
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalid("invalid step-up token") from exc
    if payload.get("typ") != "stepup":
        raise TokenInvalid("wrong token type")
    try:
        return uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenInvalid("malformed subject claim") from exc


# ---------------------------------------------------------------- refresh


def _family_key(family_id: str) -> str:
    """Redis key rfam:{id} → user_id for one refresh family."""
    return f"rfam:{family_id}"


def _token_key(token: str) -> str:
    """Redis key rtk:{token} → family_id for a live refresh token."""
    return f"rtk:{token}"


def _tombstone_key(token: str) -> str:
    """Redis key rtold:{token} → family_id after a token was rotated or logged out."""
    return f"rtold:{token}"


async def issue_refresh_family(redis: Redis, user_id: uuid.UUID) -> str:
    """Mint a fresh refresh token in a new family.

    Flow:
        1. New family_id + urlsafe token.
        2. SET rfam and rtk with 7d+60s TTL.
        3. Return the opaque token (not stored in the JWT).

    Debug:
        Missing cookie after login → router _set_refresh_cookie, not this function.
    """
    family_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(48)
    pipe = redis.pipeline()
    pipe.set(_family_key(family_id), str(user_id), ex=REFRESH_TTL_SECONDS + 60)
    pipe.set(_token_key(token), family_id, ex=REFRESH_TTL_SECONDS + 60)
    await pipe.execute()
    return token


# 7 days + 60s so the family outlives its tokens' legal lifetime by a hair.
REFRESH_TTL_SLACK_SAFE = REFRESH_TTL_SECONDS + 60


async def rotate_refresh_token(redis: Redis, token: str) -> tuple[str, uuid.UUID]:
    """Rotate: retire the presented token, mint a sibling in the same family.

    Flow:
        1. Tombstone hit → _kill_family then RefreshReuseDetected.
        2. No live rtk key → TokenInvalid (unknown/expired).
        3. Family key missing → delete stray token, TokenInvalid.
        4. Delete old rtk, write tombstone, write new rtk; return (new_token, user_id).

    Debug:
        401 REFRESH_REUSE_DETECTED means a rotated token was replayed. Family siblings
        also die — check rtk:* scan in _kill_family.
    """
    if await redis.exists(_tombstone_key(token)):
        family_id = await redis.get(_tombstone_key(token))
        if family_id:
            await _kill_family(redis, str(family_id))
        raise RefreshReuseDetected("refresh token reuse — family revoked")

    family_id = await redis.get(_token_key(token))
    if family_id is None:
        raise TokenInvalid("unknown or expired refresh token")

    user_id_raw = await redis.get(_family_key(str(family_id)))
    if user_id_raw is None:
        await redis.delete(_token_key(token))
        raise TokenInvalid("refresh family expired")

    user_id = uuid.UUID(str(user_id_raw))
    new_token = secrets.token_urlsafe(48)
    pipe = redis.pipeline()
    # Retire: tombstone keeps family id so a replay can be traced + punished.
    pipe.delete(_token_key(token))
    pipe.set(_tombstone_key(token), str(family_id), ex=REFRESH_TTL_SECONDS + 60)
    pipe.set(_token_key(new_token), str(family_id), ex=REFRESH_TTL_SECONDS + 60)
    await pipe.execute()
    return new_token, user_id


async def _kill_family(redis: Redis, family_id: str) -> None:
    """Remove every live token of the family (the rotation-kill).

    Flow:
        1. SCAN rtk:*; delete keys whose value equals family_id.
        2. Delete rfam:{family_id}. Tombstones are left so later replays still detect reuse.

    Debug:
        Slow logout/reuse on large Redis → scan_iter count=500. Family still works →
        a token value did not equal family_id (type mismatch).
    """
    # Tombstones retain family id for audit; live tokens are what we revoke.
    keys: list[str] = []
    async for key in redis.scan_iter(match="rtk:*", count=500):
        val = await redis.get(key)
        if val == family_id:
            keys.append(key)
    if keys:
        await redis.delete(*keys)
    await redis.delete(_family_key(family_id))


async def revoke_refresh_token(redis: Redis, token: str) -> None:
    """Logout: retire the presented token (its family keeps siblings).

    Flow:
        1. No live rtk → return (already logged out).
        2. Delete rtk and write tombstone pointing at the same family.

    Debug:
        Unlike _kill_family, other devices in the family stay logged in.
    """
    family_id = await redis.get(_token_key(token))
    if family_id is None:
        return
    pipe = redis.pipeline()
    pipe.delete(_token_key(token))
    pipe.set(_tombstone_key(token), str(family_id), ex=REFRESH_TTL_SECONDS + 60)
    await pipe.execute()
