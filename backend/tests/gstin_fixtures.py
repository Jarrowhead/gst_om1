"""Synthetic GSTIN/PAN fixture helpers (AI_BUILD_PLAYBOOK hard rule #1).

Generated GSTINs are structurally valid and pass the mod-36 checksum digit
(same algorithm as scripts/measure_extraction.py); the PAN is embedded at
GSTIN[2:12] so PAN cross-checks hold. Randomized per call so shared-DB test
runs never collide on unique columns. These are FIXTURE identities only —
never ship them as real taxpayer data.
"""

from __future__ import annotations

import random
import re
import string

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_WEIGHTS = (1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2)
_STATE_CODES = ("27", "29", "07", "06", "24", "29", "27")  # weighted common states
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

_rng = random.SystemRandom()


def make_pan() -> str:
    """Random structurally-valid PAN: AAA-PP-AAA -> 5 letters, 4 digits, 1 letter."""
    return (
        "".join(_rng.choice(string.ascii_uppercase) for _ in range(5))
        + "".join(_rng.choice(string.digits) for _ in range(4))
        + _rng.choice(string.ascii_uppercase)
    )


def _check_digit(first14: str) -> str:
    """Mod-36 checksum character over the first 14 GSTIN chars.

    Implements the GSTN-spec ISO 7064 MOD 37-36 complement form:
    check = CHARSET[(36 - total % 36) % 36].
    Verified against worked example 27AAPFU0939F1ZV (sum 221 -> V)
    and unissued checksum-valid vectors 27AAACR5055K1Z7, 00AAACR5055K1ZN.
    """
    total = 0
    for i, ch in enumerate(first14):
        prod = _CHARSET.index(ch) * _WEIGHTS[i]
        total += prod // 36 + prod % 36
    return _CHARSET[(36 - total % 36) % 36]


def make_gstin(
    pan: str | None = None, state_code: str | None = None, entity_code: str = "1"
) -> str:
    """Checksum-valid 15-char GSTIN: state(2) + PAN(10) + entity(1) + Z + check.

    The PAN is embedded at positions 3-12, so GSTIN[2:12] == pan always.
    """
    pan = pan or make_pan()
    state = state_code or _rng.choice(_STATE_CODES)
    first14 = f"{state}{pan}{entity_code}Z"
    return first14 + _check_digit(first14)


def gstin_checksum_valid(gstin: str) -> bool:
    """Validate structure + mod-36 checksum digit (independent re-implementation)."""
    g = (gstin or "").upper().strip()
    if not _GSTIN_RE.fullmatch(g):
        return False
    total = 0
    for i, ch in enumerate(g[:14]):
        prod = _CHARSET.index(ch) * _WEIGHTS[i]
        total += prod // 36 + prod % 36
    return _CHARSET[(36 - total % 36) % 36] == g[14]
