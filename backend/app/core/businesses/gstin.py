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
    total = 0
    for i, ch in enumerate(first14):
        prod = _CHARSET.index(ch) * _WEIGHTS[i]
        total += prod // 36 + prod % 36
    return _CHARSET[(36 - total % 36) % 36]


def validate_pan(pan: str) -> str:
    """Return uppercased, regex-valid PAN or raise ValueError."""
    p = (pan or "").upper().strip()
    if not _PAN_RE.fullmatch(p):
        raise ValueError("invalid PAN format")
    return p


def validate_gstin(gstin: str, pan: str | None = None) -> str:
    """Return uppercased, checksum-valid GSTIN; optionally enforce PAN[2:12]."""
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
    """First two characters of a validated GSTIN."""
    return validate_gstin(gstin)[:2]
