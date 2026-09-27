"""TOTP (time-based 2FA) service — mandatory for every ca_firm_member.

SECURITY_AND_ACCESS.md §1: "TOTP mandatory for every ca_firm_member — enforced
at firm-join, not optional. Setup flow: QR + verify code before membership
activates." pyotp supplies the RFC 6238 implementation; the secret lives on the
users row (totp_secret / totp_enabled_at) until the firm-join guard task wires
the enforcement point.
"""

from __future__ import annotations

import base64

import pyotp

from app.core.auth.errors import TotpAlreadyEnabled, TotpInvalid


def generate_secret() -> str:
    """New base32 TOTP secret (stored until verify succeeds)."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, user_identifier: str, issuer: str = "GST-Filing") -> str:
    """otpauth:// URI the frontend renders as a QR code."""
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=user_identifier, issuer_name=issuer
    )


def verify_code(secret: str, code: str, valid_window: int = 1) -> bool:
    """Check a 6-digit code (±1 30s window for clock drift)."""
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)


async def enable_totp(secret: str, code: str) -> bool:
    """Client-side verify-before-enable: only a valid code flips the flag.

    Returns True on success; raises TotpInvalid otherwise. The caller persists
    totp_secret/totp_enabled_at inside the same transaction.
    """
    if not verify_code(secret, code):
        raise TotpInvalid("invalid TOTP code")
    return True


def is_enabled(secret: str | None, enabled_at: object) -> bool:
    """True when the user has completed TOTP setup and verification."""
    return secret is not None and enabled_at is not None


def assert_not_enabled(secret: str | None, enabled_at: object) -> None:
    """409 if TOTP is already active for the user."""
    if secret is not None and enabled_at is not None:
        raise TotpAlreadyEnabled("TOTP is already enabled for this account")


def secret_qr_png_data_uri(secret: str, user_identifier: str) -> str:
    """Dev convenience: QR payload as a base64 data URI (no extra dependency).

    The frontend (task 0.7) renders the real QR client-side from the `qr_uri`
    returned by /auth/totp/setup; this helper is the server-side encoding of
    the same otpauth URI.
    """
    uri = provisioning_uri(secret, user_identifier)
    return "data:text/plain;base64," + base64.b64encode(uri.encode()).decode()
