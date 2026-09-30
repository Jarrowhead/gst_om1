"""GSTIN + PAN validation: regex, mod-36 checksum, PAN cross-check.

Ported from scripts/measure_extraction.py and backend/tests/gstin_fixtures.py
with the same mod-36 ISO 7064 complement algorithm; verified against published
GSTN worked example 27AAPFU0939F1ZV and additional checksum-valid vectors.
"""

from __future__ import annotations

import re

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_WEIGHTS = (1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2)
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _check_digit(first14: str) -> str:
    """ISO 7064 mod-36 complement check character for the first 14 GSTIN chars.

    Flow:
        1. For each char, value * weight, fold prod//36 + prod%36 into total.
        2. Return CHARSET[(36 - total%36) % 36].

    Debug:
        Worked example 27AAPFU0939F1ZV must yield V. Do not use the harness in measure_extraction.py.
    """
    total = 0
    for i, ch in enumerate(first14):
        prod = _CHARSET.index(ch) * _WEIGHTS[i]
        total += prod // 36 + prod % 36
    return _CHARSET[(36 - total % 36) % 36]


def validate_pan(pan: str) -> str:
    """Return uppercased, regex-valid PAN or raise ValueError.

    Flow:
        Upper/strip, match ^[A-Z]{5}[0-9]{4}[A-Z]$, else 'invalid PAN format'.

    Debug:
        API maps ValueError to 422 VALIDATION_ERROR via value_error_handler.
    """
    p = (pan or "").upper().strip()
    if not _PAN_RE.fullmatch(p):
        raise ValueError("invalid PAN format")
    return p


def validate_gstin(gstin: str, pan: str | None = None) -> str:
    """Return uppercased, checksum-valid GSTIN; optionally enforce PAN embedded at [2:12].

    Flow:
        1. Regex (entity char is Z in this pattern).
        2. Recompute check digit; mismatch → 'invalid GSTIN checksum'.
        3. If pan given, g[2:12] must equal validate_pan(pan).

    Debug:
        'PAN does not match GSTIN positions 3-12' is 1-based wording for slice [2:12].
    """
    g = (gstin or "").upper().strip()
    if not _GSTIN_RE.fullmatch(g):
        raise ValueError("invalid GSTIN format")
    total = 0
    for i, ch in enumerate(g[:14]):
        prod = _CHARSET.index(ch) * _WEIGHTS[i]
        total += prod // 36 + prod % 36
    if _CHARSET[(36 - total % 36) % 36] != g[14]:
        raise ValueError("invalid GSTIN checksum")
    if pan is not None and g[2:12] != validate_pan(pan):
        raise ValueError("PAN does not match GSTIN positions 3-12")
    return g


def gstin_state_code(gstin: str) -> str:
    """First two characters of a validated GSTIN (state code).

    Flow:
        validate_gstin (checksum, no PAN cross-check) then slice [:2].

    Debug:
        ValueError here means the GSTIN itself is bad, not the state lookup table.
    """
    return validate_gstin(gstin)[:2]
