"""OTP service: 6-digit codes, 5-min expiry, attempt counter, 5/hour/identifier.

Redis keys (SECURITY_AND_ACCESS.md §1):
  otp:{identifier}           -> {code, attempts, purpose}  TTL 300s
  otp_rl:{identifier}        -> request counter            TTL 3600s
Dev mode returns the code so tests/dev can login without an SMS provider.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import get_settings
from app.core.auth.errors import (
    IdentifierInvalid,
    OtpExpired,
    OtpInvalid,
    OtpRateLimited,
    OtpTooManyAttempts,
)

OTP_TTL_SLACK_SECONDS = 5  # keep the rate-limit window tidy around expiry


@dataclass(frozen=True)
class OtpRequest:
    """Result of requesting an OTP for an identifier."""

    otp_sent: bool
    dev_otp: str | None  # echoed only in dev mode (SMS provider is Phase 1)


def validate_identifier(identifier: str) -> str:
    """Accept a 10-13 digit mobile or an email; else 422."""
    digits = identifier.replace("+", "").strip()
    if digits.isdigit() and 10 <= len(digits) <= 13:
        return identifier.strip()
    if "@" in identifier and "." in identifier.split("@")[-1]:
        return identifier.strip()
    raise IdentifierInvalid(
        "identifier must be a mobile number or email address"
    )


def _otp_key(identifier: str) -> str:
    return f"otp:{identifier}"


def _rate_key(identifier: str) -> str:
    return f"otp_rl:{identifier}"


async def request_otp(redis: Redis, identifier: str, purpose: str) -> OtpRequest:
    """Issue an OTP (5/hour/identifier) and store it with a 5-min TTL."""
    settings = get_settings()
    identifier = validate_identifier(identifier)

    count = await redis.incr(_rate_key(identifier))
    if count == 1:
        await redis.expire(_rate_key(identifier), 3600)
    if count > settings.otp_request_limit_per_hour:
        raise OtpRateLimited("OTP request limit exceeded (5/hour per identifier)")

    code = "".join(secrets.choice("0123456789") for _ in range(settings.otp_length))
    await redis.set(
        _otp_key(identifier),
        json.dumps({"code": code, "attempts": 0, "purpose": purpose}),
        ex=settings.otp_expiry_seconds,
    )
    return OtpRequest(otp_sent=True, dev_otp=code if settings.dev_mode else None)


async def verify_otp(redis: Redis, identifier: str, otp: str) -> str:
    """Verify the code; returns the OTP's purpose (LOGIN or REGISTER).

    Failure paths in order: expired -> wrong code (attempts++) -> attempts
    exhausted (kill the OTP) -> success deletes the OTP (single use).
    """
    settings = get_settings()
    identifier = validate_identifier(identifier)
    raw = await redis.get(_otp_key(identifier))
    if raw is None:
        raise OtpExpired("no active OTP for this identifier")

    record: dict[str, object] = json.loads(raw)
    attempts = int(str(record["attempts"])) + 1
    if record["code"] != otp:
        if attempts >= settings.otp_max_verify_attempts:
            await redis.delete(_otp_key(identifier))
            raise OtpTooManyAttempts("OTP verify attempts exhausted")
        await redis.set(
            _otp_key(identifier),
            json.dumps({**record, "attempts": attempts}),
            ex=settings.otp_expiry_seconds + OTP_TTL_SLACK_SECONDS,
        )
        raise OtpInvalid("incorrect OTP")
    await redis.delete(_otp_key(identifier))
    return str(record["purpose"])
