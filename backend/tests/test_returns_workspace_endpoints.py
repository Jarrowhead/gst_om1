"""Test returns workspace endpoints for Phase 8/11.

Ensures that all 6 workspace API endpoints required by the frontend returns workspace:
  1. GET /api/v1/gst-accounts/{gstin}/months/{fp}/summary
  2. GET /api/v1/gst-accounts/{gstin}/months/{fp}/gstr1/export.json
  3. GET /api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/export.json
  4. GET /api/v1/gst-accounts/{gstin}/months/{fp}/gstr1/hsn-summary
  5. GET /api/v1/gst-accounts/{gstin}/months/{fp}/returns/validation
  6. GET /api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/setoff
as well as the server-rendered Excel exports:
  7. GET /api/v1/gst-accounts/{gstin}/months/{fp}/gstr1/export.xlsx
  8. GET /api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/export.xlsx
are registered, functional, and return HTTP 200 OK (not 404) on an empty/fresh period.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.auth.tokens import create_access_token
from app.db.models.core import AccessRole, User, UserGstAccess
from app.db.models.gst import GstAccount
from tests.auth_helpers import SessionMaker

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _checksum_gstin(state: str = "27", pan_body: str = "AAPFU0939F") -> str:
    first14 = f"{state}{pan_body}1Z"
    total = 0
    for i, ch in enumerate(first14):
        prod = CHARSET.index(ch) * (1 if i % 2 == 0 else 2)
        total += prod // 36 + prod % 36
    return first14 + CHARSET[(36 - total % 36) % 36]


async def _seed_user_and_gstin(
    sessionmaker: SessionMaker,
) -> tuple[User, str, dict[str, str]]:
    gstin = _checksum_gstin()
    async with sessionmaker() as session:
        user = User(
            email=f"tester_{uuid.uuid4().hex[:8]}@example.com",
            full_name="Tester",
        )
        session.add(user)
        await session.flush()

        account = await session.get(GstAccount, gstin)
        if account is None:
            account = GstAccount(
                gstin=gstin,
                legal_name="Workspace Test Enterprise",
            )
            session.add(account)
            await session.flush()

        session.add(
            UserGstAccess(
                user_id=user.id,
                gstin=gstin,
                role=AccessRole.ADMIN,
            )
        )
        await session.commit()

    token = create_access_token(user.id)
    headers = {"Authorization": f"Bearer {token}"}
    return user, gstin, headers


@pytest.mark.asyncio
async def test_returns_workspace_all_six_endpoints_return_200(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    """Verify all 6 returns workspace routes return 200 OK without 404 on clean period."""
    _, gstin, headers = await _seed_user_and_gstin(api_sessionmaker)
    fp = "092026"

    # 1. Month summary
    r_sum = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/summary",
        headers=headers,
    )
    assert r_sum.status_code == 200, f"summary failed: {r_sum.text}"
    body_sum = r_sum.json()
    assert body_sum["success"] is True
    assert "confirmed_count" in body_sum["data"]

    # 2. GSTR-1 JSON export
    r_r1 = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr1/export.json",
        headers=headers,
    )
    assert r_r1.status_code == 200, f"gstr1 export failed: {r_r1.text}"
    body_r1 = r_r1.json()
    assert body_r1["success"] is True
    assert body_r1["data"]["gstin"] == gstin

    # 3. GSTR-3B JSON export
    r_r3b = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/export.json",
        headers=headers,
    )
    assert r_r3b.status_code == 200, f"gstr3b export failed: {r_r3b.text}"
    body_r3b = r_r3b.json()
    assert body_r3b["success"] is True
    assert body_r3b["data"]["gstin"] == gstin

    # 4. HSN summary (MUST NOT 404)
    r_hsn = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr1/hsn-summary",
        headers=headers,
    )
    assert r_hsn.status_code == 200, f"hsn-summary returned {r_hsn.status_code}: {r_hsn.text}"
    body_hsn = r_hsn.json()
    assert body_hsn["success"] is True
    assert "rows" in body_hsn["data"]
    assert "totals" in body_hsn["data"]

    # 5. Returns validation (MUST NOT 404)
    r_val = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/returns/validation",
        headers=headers,
    )
    assert r_val.status_code == 200, f"validation returned {r_val.status_code}: {r_val.text}"
    body_val = r_val.json()
    assert body_val["success"] is True
    assert "blocking" in body_val["data"]
    assert "warnings" in body_val["data"]

    # 6. Rule 88A setoff matrix (MUST NOT 404)
    r_setoff = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/setoff",
        headers=headers,
    )
    assert r_setoff.status_code == 200, f"setoff returned {r_setoff.status_code}: {r_setoff.text}"
    body_setoff = r_setoff.json()
    assert body_setoff["success"] is True
    assert "challan" in body_setoff["data"]
    assert "matrix" in body_setoff["data"]


@pytest.mark.asyncio
async def test_returns_workspace_excel_exports_openpyxl(
    client: AsyncClient,
    api_sessionmaker: SessionMaker,
) -> None:
    """Verify Excel .xlsx generation works using openpyxl without missing dependency error."""
    _, gstin, headers = await _seed_user_and_gstin(api_sessionmaker)
    fp = "092026"

    # GSTR-1 Excel
    r_x1 = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr1/export.xlsx",
        headers=headers,
    )
    assert r_x1.status_code == 200
    assert "spreadsheetml.sheet" in r_x1.headers.get("content-type", "")
    assert len(r_x1.content) > 1000  # Valid binary zip/xlsx structure

    # GSTR-3B Excel
    r_x3b = await client.get(
        f"/api/v1/gst-accounts/{gstin}/months/{fp}/gstr3b/export.xlsx",
        headers=headers,
    )
    assert r_x3b.status_code == 200
    assert "spreadsheetml.sheet" in r_x3b.headers.get("content-type", "")
    assert len(r_x3b.content) > 1000
