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
    return dt.datetime.now(tz=dt.UTC)


def create_access_token(user_id: uuid.UUID) -> str:
    """15-min JWT access token (SECURITY §1)."""
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
    """Validate signature/expiry/typ; return the user id."""
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
    """15-min step-up proof for sensitive routes (SECURITY §1 step-up list)."""
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
    """Validate a step-up token; returns the user id."""
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
    return f"rfam:{family_id}"


def _token_key(token: str) -> str:
    return f"rtk:{token}"


def _tombstone_key(token: str) -> str:
    return f"rtold:{token}"


async def issue_refresh_family(redis: Redis, user_id: uuid.UUID) -> str:
    """Mint a fresh refresh token in a new family."""
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

    Reuse detection: a retired tombstone hit means this token was already
    rotated once — replay -> kill the whole family.
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
    """Remove every live token of the family (the rotation-kill)."""
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
    """Logout: retire the presented token (its family keeps siblings)."""
    family_id = await redis.get(_token_key(token))
    if family_id is None:
        return
    pipe = redis.pipeline()
    pipe.delete(_token_key(token))
    pipe.set(_tombstone_key(token), str(family_id), ex=REFRESH_TTL_SECONDS + 60)
    await pipe.execute()
