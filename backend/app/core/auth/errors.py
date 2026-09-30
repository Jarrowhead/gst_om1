"""Auth domain errors — mapped to the API error envelope in app/api/errors.py."""

from __future__ import annotations


class AuthError(Exception):
    """Base: carries an HTTP status and the envelope error code."""

    status_code = 401
    code = "AUTH_ERROR"

    def __init__(self, message: str) -> None:
        """Store message for the API envelope; status_code/code come from the subclass.

        Flow:
            super() + self.message. auth_error_handler reads code, message, status_code.

        Debug:
            Missing code on a new subclass falls back to AUTH_ERROR / 401 from this base.
        """
        super().__init__(message)
        self.message = message


class OtpInvalid(AuthError):
    status_code = 401
    code = "OTP_INVALID"


class OtpExpired(AuthError):
    status_code = 401
    code = "OTP_EXPIRED"


class OtpTooManyAttempts(AuthError):
    """Verify-attempt counter exhausted for this OTP."""

    status_code = 429
    code = "OTP_TOO_MANY_ATTEMPTS"


class OtpRateLimited(AuthError):
    """>5 request/hour/identifier (SECURITY §1)."""

    status_code = 429
    code = "OTP_RATE_LIMITED"


class InvalidCredentials(AuthError):
    status_code = 401
    code = "INVALID_CREDENTIALS"


class TokenInvalid(AuthError):
    status_code = 401
    code = "TOKEN_INVALID"


class RefreshReuseDetected(AuthError):
    """A rotated token was replayed — the whole family is killed."""

    status_code = 401
    code = "REFRESH_REUSE_DETECTED"


class TotpRequired(AuthError):
    """CA-flow action attempted without TOTP enabled."""

    status_code = 403
    code = "TOTP_REQUIRED"


class TotpInvalid(AuthError):
    status_code = 401
    code = "TOTP_INVALID"


class TotpAlreadyEnabled(AuthError):
    status_code = 409
    code = "TOTP_ALREADY_ENABLED"


class StepUpRequired(AuthError):
    status_code = 403
    code = "STEP_UP_REQUIRED"


class IdentifierInvalid(AuthError):
    """Identifier is neither a valid mobile nor email."""

    status_code = 422
    code = "IDENTIFIER_INVALID"
