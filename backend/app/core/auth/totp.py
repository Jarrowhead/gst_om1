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
    """New base32 TOTP secret (stored until verify succeeds).

    Flow:
        pyotp.random_base32() — persisted on users.totp_secret before totp_enabled_at is set.

    Debug:
        Setup twice after enable raises TotpAlreadyEnabled in assert_not_enabled, not here.
    """
    return pyotp.random_base32()


def provisioning_uri(secret: str, user_identifier: str, issuer: str = "GST-Filing") -> str:
    """otpauth:// URI the frontend renders as a QR code.

    Flow:
        TOTP(secret).provisioning_uri(name=identifier, issuer=GST-Filing).

    Debug:
        Authenticator rejects the QR → secret/issuer mismatch vs totpCode() in frontend/tests.
    """
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=user_identifier, issuer_name=issuer
    )


def verify_code(secret: str, code: str, valid_window: int = 1) -> bool:
    """Check a 6-digit code (±1 30s window for clock drift).

    Flow:
        pyotp.TOTP.verify with valid_window (default 1 step = ±30s).

    Debug:
        False near a 30s boundary → clock skew larger than the window.
    """
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)


async def enable_totp(secret: str, code: str) -> bool:
    """Verify a code before the caller persists totp_enabled_at.

    Flow:
        1. verify_code; failure → TotpInvalid.
        2. Return True. Caller writes totp_secret and totp_enabled_at in one transaction.

    Debug:
        This helper does not touch the DB. If the flag stays null, look at totp_verify in service.py.
    """
    if not verify_code(secret, code):
        raise TotpInvalid("invalid TOTP code")
    return True


def is_enabled(secret: str | None, enabled_at: object) -> bool:
    """True when the user has completed TOTP setup and verification.

    Flow:
        Both totp_secret and totp_enabled_at must be set. Secret alone is pending setup.

    Debug:
        Firm create 403 TOTP_REQUIRED while QR was shown → verify step never set enabled_at.
    """
    return secret is not None and enabled_at is not None


def assert_not_enabled(secret: str | None, enabled_at: object) -> None:
    """409 if TOTP is already active for the user.

    Flow:
        Raise TotpAlreadyEnabled when secret and enabled_at are both set.

    Debug:
        Re-running /auth/totp/setup after success hits this, not generate_secret.
    """
    if secret is not None and enabled_at is not None:
        raise TotpAlreadyEnabled("TOTP is already enabled for this account")


def secret_qr_png_data_uri(secret: str, user_identifier: str) -> str:
    """Dev convenience: otpauth URI as a base64 data URI (not a PNG image).

    Flow:
        1. provisioning_uri.
        2. Prefix data:text/plain;base64,.

    Debug:
        UI QR comes from qr_uri on /auth/totp/setup, not this helper. Name says png but payload is text.
    """
    uri = provisioning_uri(secret, user_identifier)
    return "data:text/plain;base64," + base64.b64encode(uri.encode()).decode()
