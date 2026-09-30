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
    """Random structurally-valid PAN: 5 letters, 4 digits, 1 letter.

    Flow:
        SystemRandom draws from ascii uppercase and digits. Not a real taxpayer PAN.

    Debug:
        Unique per call so parallel tests do not collide on businesses.pan.
    """
    return (
        "".join(_rng.choice(string.ascii_uppercase) for _ in range(5))
        + "".join(_rng.choice(string.digits) for _ in range(4))
        + _rng.choice(string.ascii_uppercase)
    )


def _check_digit(first14: str) -> str:
    """Mod-36 complement check character for 14 GSTIN chars.

    Flow:
        Fold value*weight, return CHARSET[(36 - total%36) % 36].

    Debug:
        Must match app.core.businesses.gstin._check_digit. Example 27AAPFU0939F1Z → V.
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

    Flow:
        1. pan or make_pan(); state from the weighted list if omitted.
        2. first14 = state + pan + entity + 'Z'; append _check_digit.

    Debug:
        GSTIN[2:12] always equals the PAN. Pass the same pan into create_business.
    """
    pan = pan or make_pan()
    state = state_code or _rng.choice(_STATE_CODES)
    first14 = f"{state}{pan}{entity_code}Z"
    return first14 + _check_digit(first14)


def gstin_checksum_valid(gstin: str) -> bool:
    """True when structure and mod-36 complement both match.

    Flow:
        Regex, recompute check digit, compare to the 15th character.

    Debug:
        Independent of the app helper so a broken production checksum still fails tests.
    """
    g = (gstin or "").upper().strip()
    if not _GSTIN_RE.fullmatch(g):
        return False
    total = 0
    for i, ch in enumerate(g[:14]):
        prod = _CHARSET.index(ch) * _WEIGHTS[i]
        total += prod // 36 + prod % 36
    return _CHARSET[(36 - total % 36) % 36] == g[14]
